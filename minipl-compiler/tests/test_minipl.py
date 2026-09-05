"""Тесты компилятора Mini-PL.

Запуск:  python3 -m unittest discover -s tests -v   (из каталога minipl-compiler)
"""

import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import minipl as mp


def compile_text(text, name="<test>"):
    return mp.compile_source(mp.Source(text, name))


def run(text, stdin=""):
    """Компилирует и выполняет программу, возвращая (вывод, компиляция)."""

    compilation = compile_text(text)
    if compilation.chunk is None:
        raise AssertionError(
            "программа не скомпилировалась: "
            + "; ".join(d.message for d in compilation.diagnostics.errors)
        )
    output = io.StringIO()
    lines = iter(stdin.splitlines(True))
    machine = mp.VM(
        compilation.chunk,
        output=output.write,
        input_source=mp.InputBuffer(lambda: next(lines, "")),
        max_steps=200000,
    )
    machine.run()
    return output.getvalue(), compilation


def error_codes(text):
    compilation = compile_text(text)
    return [d.code for d in compilation.diagnostics.errors]


def warning_codes(text):
    compilation = compile_text(text)
    return [d.code for d in compilation.diagnostics.warnings]


class LexerTests(unittest.TestCase):
    def test_basic_token_stream(self):
        tokens = mp.tokenize(mp.Source('var x : int := 12;', "t"))
        self.assertEqual(
            [token.type for token in tokens],
            [
                mp.TokenType.VAR,
                mp.TokenType.IDENTIFIER,
                mp.TokenType.COLON,
                mp.TokenType.INT,
                mp.TokenType.ASSIGN,
                mp.TokenType.INT_LITERAL,
                mp.TokenType.SEMICOLON,
                mp.TokenType.EOF,
            ],
        )

    def test_nested_block_comment(self):
        tokens = mp.tokenize(mp.Source("/* a /* b */ c */ print", "t"))
        self.assertEqual(tokens[0].type, mp.TokenType.PRINT)

    def test_line_comment_and_positions(self):
        tokens = mp.tokenize(mp.Source("// комментарий\nprint 1;", "t"))
        self.assertEqual(tokens[0].span.start.line, 2)
        self.assertEqual(tokens[0].span.start.column, 1)

    def test_string_escapes(self):
        tokens = mp.tokenize(mp.Source(r'"a\nb\t\"c\\"', "t"))
        self.assertEqual(tokens[0].value, 'a\nb\t"c\\')

    def test_two_character_operators(self):
        types = [t.type for t in mp.tokenize(mp.Source("<= >= <> := ..", "t"))]
        self.assertEqual(
            types[:5],
            [
                mp.TokenType.LESS_EQUAL,
                mp.TokenType.GREATER_EQUAL,
                mp.TokenType.NOT_EQUAL,
                mp.TokenType.ASSIGN,
                mp.TokenType.RANGE,
            ],
        )

    def test_unterminated_string_is_reported(self):
        bag = mp.DiagnosticBag()
        mp.tokenize(mp.Source('print "abc;', "t"), bag)
        self.assertIn(mp.Code.UNTERMINATED_STRING, [d.code for d in bag.errors])

    def test_unterminated_comment_is_reported(self):
        bag = mp.DiagnosticBag()
        mp.tokenize(mp.Source("/* хвост", "t"), bag)
        self.assertIn(mp.Code.UNTERMINATED_COMMENT, [d.code for d in bag.errors])

    def test_unknown_character_is_reported(self):
        bag = mp.DiagnosticBag()
        mp.tokenize(mp.Source("print 1 $ 2;", "t"), bag)
        self.assertIn(mp.Code.UNKNOWN_CHARACTER, [d.code for d in bag.errors])

    def test_integer_overflow_literal(self):
        bag = mp.DiagnosticBag()
        mp.tokenize(mp.Source("print 99999999999;", "t"), bag)
        self.assertIn(mp.Code.INT_OVERFLOW, [d.code for d in bag.errors])

    def test_lexer_collects_several_errors(self):
        bag = mp.DiagnosticBag()
        mp.tokenize(mp.Source("print 1 $ 2 # 3;", "t"), bag)
        self.assertEqual(len(bag.errors), 2)


class ParserTests(unittest.TestCase):
    def test_operator_precedence(self):
        output, _ = run("print 1 + 2 * 3;")
        self.assertEqual(output, "7")

    def test_parentheses_change_precedence(self):
        output, _ = run("print (1 + 2) * 3;")
        self.assertEqual(output, "9")

    def test_comparison_is_weaker_than_arithmetic(self):
        output, _ = run("print 1 + 1 = 2;")
        self.assertEqual(output, "true")

    def test_missing_semicolon(self):
        self.assertIn(mp.Code.MISSING_SEMICOLON, error_codes("print 1\nprint 2;"))

    def test_unclosed_parenthesis(self):
        self.assertIn(mp.Code.UNCLOSED_PAREN, error_codes("print (1 + 2;"))

    def test_assignment_with_single_equals_hints(self):
        compilation = compile_text("var x : int := 0;\nx = 5;")
        hints = [d.hint for d in compilation.diagnostics.errors if d.hint]
        self.assertTrue(any("':='" in hint for hint in hints))

    def test_error_recovery_finds_several_problems(self):
        codes = error_codes("print ;\nprint ;\nprint ;")
        self.assertEqual(len(codes), 3)

    def test_unclosed_for_is_reported(self):
        self.assertTrue(error_codes("for i in 1..3 do\n print i;\n"))


class SemanticTests(unittest.TestCase):
    def test_undeclared_variable(self):
        self.assertIn(mp.Code.UNDECLARED_VARIABLE, error_codes("print y;"))

    def test_undeclared_variable_suggests_similar_name(self):
        compilation = compile_text("var count : int := 1;\nprint cout;")
        hints = [d.hint or "" for d in compilation.diagnostics.errors]
        self.assertTrue(any("count" in hint for hint in hints))

    def test_redeclaration(self):
        self.assertIn(
            mp.Code.REDECLARED_VARIABLE, error_codes("var x : int;\nvar x : int;")
        )

    def test_type_mismatch_in_initializer(self):
        self.assertIn(mp.Code.TYPE_MISMATCH, error_codes('var x : int := "s";'))

    def test_type_mismatch_in_assignment(self):
        self.assertIn(
            mp.Code.TYPE_MISMATCH, error_codes('var x : int := 0;\nx := true;')
        )

    def test_mixed_operand_types(self):
        self.assertIn(mp.Code.TYPE_MISMATCH, error_codes('print 1 + "a";'))

    def test_bad_operand_type(self):
        self.assertIn(mp.Code.BAD_OPERAND_TYPE, error_codes('print "a" * "b";'))

    def test_unary_not_on_int(self):
        self.assertIn(mp.Code.BAD_OPERAND_TYPE, error_codes("print !1;"))

    def test_assign_to_loop_variable(self):
        codes = error_codes("for i in 1..3 do\n i := 5;\nend for;")
        self.assertIn(mp.Code.ASSIGN_TO_LOOP_VARIABLE, codes)

    def test_read_into_loop_variable(self):
        codes = error_codes("for i in 1..3 do\n read i;\nend for;")
        self.assertIn(mp.Code.ASSIGN_TO_LOOP_VARIABLE, codes)

    def test_non_int_loop_range(self):
        codes = error_codes('for i in 1.."x" do\n print i;\nend for;')
        self.assertIn(mp.Code.NON_INT_LOOP_RANGE, codes)

    def test_assert_requires_bool(self):
        self.assertIn(mp.Code.ASSERT_NOT_BOOL, error_codes("assert (1);"))

    def test_if_condition_requires_bool(self):
        self.assertIn(mp.Code.TYPE_MISMATCH, error_codes("if 1 do print 1; end if;"))

    def test_read_bool_is_rejected(self):
        self.assertIn(
            mp.Code.READ_BAD_TYPE, error_codes("var b : bool;\nread b;\nprint b;")
        )

    def test_unused_variable_warning(self):
        self.assertIn(mp.Code.UNUSED_VARIABLE, warning_codes("var x : int := 1;"))

    def test_error_does_not_cascade(self):
        # неизвестная переменная должна дать ровно одну ошибку, а не серию
        codes = error_codes("print y + y * y;")
        self.assertEqual(codes.count(mp.Code.UNDECLARED_VARIABLE), 3)
        self.assertNotIn(mp.Code.TYPE_MISMATCH, codes)

    def test_expression_types_are_annotated(self):
        compilation = compile_text("print 1 < 2;")
        statement = compilation.program.block.statements[0]
        self.assertIs(statement.value.type, mp.Type.BOOL)

    def test_symbols_get_slots(self):
        compilation = compile_text("var a : int := 1;\nvar b : int := a;")
        slots = [symbol.slot for symbol in compilation.symbols]
        self.assertEqual(slots, [0, 1])


class ExecutionTests(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(run("print 7 - 3 * 2;")[0], "1")

    def test_division_truncates_towards_zero(self):
        self.assertEqual(run("print -7 / 2;")[0], "-3")
        self.assertEqual(run("print 7 / 2;")[0], "3")

    def test_modulo_follows_division(self):
        self.assertEqual(run("print -7 % 2;")[0], "-1")
        self.assertEqual(run("print 7 % 2;")[0], "1")

    def test_string_concatenation_and_comparison(self):
        self.assertEqual(run('print "a" + "b";')[0], "ab")
        self.assertEqual(run('print "a" < "b";')[0], "true")

    def test_boolean_printing(self):
        self.assertEqual(run("print true;")[0], "true")
        self.assertEqual(run("print !true;")[0], "false")

    def test_unary_minus(self):
        self.assertEqual(run("var x : int := 5;\nprint -x;")[0], "-5")

    def test_default_values(self):
        program = "var i : int;\nvar s : string;\nvar b : bool;\nprint i; print s; print b;"
        self.assertEqual(run(program)[0], "0false")

    def test_for_loop_sum(self):
        program = "var s : int := 0;\nfor i in 1..5 do\n s := s + i;\nend for;\nprint s;"
        self.assertEqual(run(program)[0], "15")

    def test_for_loop_with_empty_range(self):
        program = "var s : int := 0;\nfor i in 5..1 do\n s := s + 1;\nend for;\nprint s;"
        self.assertEqual(run(program)[0], "0")

    def test_nested_loops(self):
        program = (
            "var s : int := 0;\n"
            "for i in 1..3 do\n"
            "  for j in 1..3 do\n"
            "    s := s + 1;\n"
            "  end for;\n"
            "end for;\n"
            "print s;"
        )
        self.assertEqual(run(program)[0], "9")

    def test_while_loop(self):
        program = "var i : int := 0;\nwhile i < 3 do\n i := i + 1;\nend while;\nprint i;"
        self.assertEqual(run(program)[0], "3")

    def test_if_else(self):
        self.assertEqual(run('if 1 > 2 do print "a"; else print "b"; end if;')[0], "b")

    def test_if_without_else(self):
        self.assertEqual(run('if 1 > 2 do print "a"; end if;\nprint "done";')[0], "done")

    def test_short_circuit_and(self):
        # правая часть привела бы к делению на ноль, но не должна вычисляться
        self.assertEqual(run("print false & (10 / 0 = 1);")[0], "false")

    def test_short_circuit_or(self):
        self.assertEqual(run("print true | (10 / 0 = 1);")[0], "true")

    def test_read_int_and_string(self):
        program = "var n : int;\nvar s : string;\nread n;\nread s;\nprint n; print s;"
        self.assertEqual(run(program, "42 abc\n")[0], "42abc")

    def test_assert_passes(self):
        self.assertEqual(run("assert (1 = 1);\nprint \"ok\";")[0], "ok")

    def test_variable_declaration_inside_loop_resets(self):
        program = (
            "var last : int := 0;\n"
            "for i in 1..3 do\n"
            "  var tmp : int;\n"
            "  tmp := tmp + i;\n"
            "  last := tmp;\n"
            "end for;\n"
            "print last;"
        )
        self.assertEqual(run(program)[0], "3")


class RuntimeErrorTests(unittest.TestCase):
    def _expect_runtime_error(self, program, code, stdin=""):
        with self.assertRaises(mp.RuntimeMiniPLError) as caught:
            run(program, stdin)
        self.assertEqual(caught.exception.diagnostic.code, code)
        return caught.exception.diagnostic

    def test_division_by_zero(self):
        diagnostic = self._expect_runtime_error(
            "var z : int := 0;\nprint 1 / z;", mp.Code.DIVISION_BY_ZERO
        )
        self.assertEqual(diagnostic.span.start.line, 2)

    def test_modulo_by_zero(self):
        self._expect_runtime_error("var z : int := 0;\nprint 1 % z;", mp.Code.DIVISION_BY_ZERO)

    def test_failed_assertion(self):
        self._expect_runtime_error("assert (1 = 2);", mp.Code.ASSERTION_FAILED)

    def test_bad_input(self):
        self._expect_runtime_error("var n : int;\nread n;\nprint n;", mp.Code.BAD_INPUT, "abc\n")

    def test_end_of_input(self):
        self._expect_runtime_error("var n : int;\nread n;\nprint n;", mp.Code.BAD_INPUT, "")

    def test_integer_overflow_at_runtime(self):
        program = "var x : int := 2147483647;\nprint x + 1;"
        self._expect_runtime_error(program, mp.Code.INT_OVERFLOW)

    def test_step_limit(self):
        compilation = compile_text("var i : int := 0;\nwhile true do\n i := i + 1;\nend while;")
        machine = mp.VM(compilation.chunk, output=lambda _: None, max_steps=500)
        with self.assertRaises(mp.RuntimeMiniPLError) as caught:
            machine.run()
        self.assertEqual(caught.exception.diagnostic.code, mp.Code.STEP_LIMIT)


class BytecodeTests(unittest.TestCase):
    def test_disassembly_mentions_variables(self):
        compilation = compile_text("var x : int := 1;\nprint x;")
        text = compilation.chunk.disassemble()
        self.assertIn("STORE", text)
        self.assertIn("x : int", text)

    def test_constants_are_deduplicated(self):
        compilation = compile_text("print 5; print 5; print 5;")
        self.assertEqual(compilation.chunk.constants.count(5), 1)

    def test_bool_and_int_constants_are_distinct(self):
        compilation = compile_text("print 1; print true;")
        self.assertEqual(len(compilation.chunk.constants), 2)

    def test_serialization_round_trip(self):
        compilation = compile_text('var s : int := 0;\nfor i in 1..4 do\n s := s + i;\nend for;\nprint s;')
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "program.mplc")
            compilation.chunk.save(path)
            restored = mp.Chunk.load(path)
        output = io.StringIO()
        mp.VM(restored, output=output.write).run()
        self.assertEqual(output.getvalue(), "10")
        self.assertEqual(len(restored.code), len(compilation.chunk.code))

    def test_line_numbers_are_preserved(self):
        compilation = compile_text("print 1;\nprint 2;")
        lines = {instruction.line for instruction in compilation.chunk.code}
        self.assertTrue({1, 2}.issubset(lines))


class DebuggerTests(unittest.TestCase):
    def _debugger(self, program, commands, stdin=""):
        compilation = compile_text(program, "debug.mpl")
        console = io.StringIO()
        queue = list(commands)

        def read_command(_prompt):
            if not queue:
                raise EOFError
            return queue.pop(0)

        def make_input():
            lines = iter(stdin.splitlines(True))
            return mp.InputBuffer(lambda: next(lines, ""))

        debugger = mp.Debugger(
            compilation,
            input_factory=make_input,
            output=console.write,
            console=console,
            read_command=read_command,
        )
        code = debugger.start()
        return console.getvalue(), debugger, code

    def test_breakpoint_stops_execution(self):
        program = "var x : int := 1;\nx := x + 1;\nprint x;"
        text, debugger, _ = self._debugger(program, ["break 2", "continue", "print x"])
        self.assertIn("x = 1", text)
        self.assertEqual(debugger.vm.slot_value("x"), 1)

    def test_conditional_breakpoint(self):
        program = "var s : int := 0;\nfor i in 1..5 do\n s := s + i;\nend for;\nprint s;"
        text, debugger, _ = self._debugger(program, ["break 3 if i = 4", "continue", "print s"])
        self.assertIn("s = 6", text)  # 1 + 2 + 3, четвёртая итерация ещё не выполнена

    def test_step_moves_one_source_line(self):
        program = "var x : int := 1;\nx := x + 1;\nprint x;"
        _, debugger, _ = self._debugger(program, ["step"])
        self.assertEqual(debugger._current_line(), 2)

    def test_stepi_moves_one_instruction(self):
        program = "print 1 + 2;"
        _, debugger, _ = self._debugger(program, ["stepi"])
        self.assertEqual(debugger.vm.pc, 1)

    def test_watch_reports_change(self):
        program = "var x : int := 1;\nx := 42;\nprint x;"
        # первое срабатывание — инициализация x значением 1, второе — присваивание 42
        text, _, _ = self._debugger(program, ["watch x", "continue", "continue"])
        self.assertIn("наблюдение: x = 1 (было 0)", text)
        self.assertIn("наблюдение: x = 42 (было 1)", text)

    def test_set_changes_variable(self):
        program = "var x : int := 1;\nprint x;"
        text, _, _ = self._debugger(program, ["step", "set x := 99", "continue"])
        self.assertIn("99", text)

    def test_set_rejects_wrong_type(self):
        program = "var x : int := 1;\nprint x;"
        text, _, _ = self._debugger(program, ["set x := true"])
        self.assertIn("тип", text)

    def test_evaluate_expression_uses_program_variables(self):
        program = "var x : int := 6;\nprint x;"
        _, debugger, _ = self._debugger(program, ["step"])
        self.assertEqual(debugger.evaluate("x * 7"), 42)

    def test_unknown_command_reports_error(self):
        text, _, _ = self._debugger("print 1;", ["чепуха"])
        self.assertIn("неизвестная команда", text)

    def test_runtime_error_sets_exit_code(self):
        program = "var z : int := 0;\nprint 1 / z;"
        text, _, code = self._debugger(program, ["continue"])
        self.assertEqual(code, 2)
        self.assertIn("E4001", text)

    def test_restart_runs_program_again(self):
        program = "var n : int;\nread n;\nprint n;"
        text, _, _ = self._debugger(program, ["continue", "run", "continue"], stdin="7\n")
        self.assertEqual(text.count("7"), 2)

    def test_breakpoint_on_line_without_code_is_rejected(self):
        text, _, _ = self._debugger("print 1;", ["break 99"])
        self.assertIn("не содержит исполняемого кода", text)

    def test_list_shows_current_line_marker(self):
        text, _, _ = self._debugger("print 1;\nprint 2;", ["list"])
        self.assertIn("=>", text)


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        self.addCleanup(self._restore)

    def _restore(self):
        sys.stdout = self._stdout
        sys.stderr = self._stderr

    def _write(self, name, text):
        path = os.path.join(self.directory.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_successful_run(self):
        path = self._write("ok.mpl", 'print "привет";')
        code = mp.main([path])
        self.assertEqual(code, mp.EXIT_OK)
        self.assertEqual(sys.stdout.getvalue(), "привет")

    def test_eval_flag(self):
        self.assertEqual(mp.main(["-e", "print 2 + 2;"]), mp.EXIT_OK)
        self.assertEqual(sys.stdout.getvalue(), "4")

    def test_compile_error_exit_code(self):
        path = self._write("bad.mpl", "print y;")
        self.assertEqual(mp.main([path]), mp.EXIT_COMPILE_ERROR)
        self.assertIn("E3001", sys.stderr.getvalue())

    def test_runtime_error_exit_code(self):
        path = self._write("boom.mpl", "assert (1 = 2);")
        self.assertEqual(mp.main([path]), mp.EXIT_RUNTIME_ERROR)
        self.assertIn("E4002", sys.stderr.getvalue())

    def test_missing_file_exit_code(self):
        self.assertEqual(mp.main([os.path.join(self.directory.name, "нет.mpl")]), mp.EXIT_USAGE_ERROR)

    def test_no_arguments_exit_code(self):
        self.assertEqual(mp.main([]), mp.EXIT_USAGE_ERROR)

    def test_check_does_not_execute(self):
        path = self._write("check.mpl", 'print "не должно печататься";')
        self.assertEqual(mp.main([path, "--check"]), mp.EXIT_OK)
        self.assertEqual(sys.stdout.getvalue(), "")

    def test_strict_turns_warnings_into_errors(self):
        path = self._write("warn.mpl", "var unused : int := 1;")
        self.assertEqual(mp.main([path, "--strict"]), mp.EXIT_COMPILE_ERROR)

    def test_no_warnings_hides_warnings(self):
        path = self._write("warn2.mpl", "var unused : int := 1;")
        self.assertEqual(mp.main([path, "--no-warnings"]), mp.EXIT_OK)
        self.assertNotIn("E3009", sys.stderr.getvalue())

    def test_stage_dumps(self):
        path = self._write("stages.mpl", "var x : int := 1;\nprint x;")
        self.assertEqual(mp.main([path, "--tokens", "--ast", "--symbols", "--dis", "--check"]), mp.EXIT_OK)
        text = sys.stdout.getvalue()
        self.assertIn("--- токены ---", text)
        self.assertIn("VarDecl", text)
        self.assertIn("--- таблица символов ---", text)
        self.assertIn("HALT", text)

    def test_emit_and_run_bytecode(self):
        path = self._write("emit.mpl", "print 21 * 2;")
        target = os.path.join(self.directory.name, "emit.mplc")
        self.assertEqual(mp.main([path, "--emit", target, "--check"]), mp.EXIT_OK)
        self.assertTrue(os.path.exists(target))
        self.assertEqual(mp.main([target, "--from-bytecode"]), mp.EXIT_OK)
        self.assertEqual(sys.stdout.getvalue(), "42")

    def test_stdin_flag_feeds_read(self):
        path = self._write("read.mpl", "var n : int;\nread n;\nprint n * 2;")
        self.assertEqual(mp.main([path, "--stdin", "21"]), mp.EXIT_OK)
        self.assertEqual(sys.stdout.getvalue(), "42")

    def test_max_steps_stops_endless_loop(self):
        path = self._write("loop.mpl", "var i : int := 0;\nwhile true do\n i := i + 1;\nend while;")
        self.assertEqual(mp.main([path, "--max-steps", "100"]), mp.EXIT_RUNTIME_ERROR)
        self.assertIn("E4005", sys.stderr.getvalue())


class ExampleProgramTests(unittest.TestCase):
    """Примеры из каталога examples должны компилироваться и работать."""

    EXAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")

    def test_all_examples_compile(self):
        for name in sorted(os.listdir(self.EXAMPLES)):
            if not name.endswith(".mpl"):
                continue
            with self.subTest(example=name):
                source = mp.Source.from_file(os.path.join(self.EXAMPLES, name))
                compilation = mp.compile_source(source)
                self.assertFalse(
                    compilation.diagnostics.has_errors(),
                    "; ".join(d.message for d in compilation.diagnostics.errors),
                )

    def test_factorial_example(self):
        source = mp.Source.from_file(os.path.join(self.EXAMPLES, "factorial.mpl"))
        compilation = mp.compile_source(source)
        output = io.StringIO()
        lines = iter(["5\n"])
        mp.VM(
            compilation.chunk,
            output=output.write,
            input_source=mp.InputBuffer(lambda: next(lines, "")),
        ).run()
        self.assertIn("120", output.getvalue())

    def test_fizzbuzz_example(self):
        source = mp.Source.from_file(os.path.join(self.EXAMPLES, "fizzbuzz.mpl"))
        compilation = mp.compile_source(source)
        output = io.StringIO()
        mp.VM(compilation.chunk, output=output.write).run()
        lines = output.getvalue().split("\n")
        self.assertEqual(lines[0], "1")
        self.assertEqual(lines[2], "Fizz")
        self.assertEqual(lines[4], "Buzz")
        self.assertEqual(lines[14], "FizzBuzz")


if __name__ == "__main__":
    unittest.main(verbosity=2)
