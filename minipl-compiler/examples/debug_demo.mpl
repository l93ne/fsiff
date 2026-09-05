/*
 * Программа с намеренной ошибкой — удобна для знакомства с отладчиком:
 *     python3 minipl.py examples/debug_demo.mpl --debug
 *     (minipl) break 14
 *     (minipl) continue
 *     (minipl) print total
 *     (minipl) watch total
 *     (minipl) continue
 * Сумма 1..5 должна быть 15, но шаг цикла учитывает не то значение.
 */
var total : int := 0;
var n : int := 5;

for i in 1..n do
    total := total + i * 2;
end for;

print "сумма 1..5 = ";
print total;
print "\n";
assert (total = 15);
