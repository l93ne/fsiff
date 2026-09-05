/* Факториал: демонстрация чтения ввода, цикла for и assert. */
var n : int;
print "n = ";
read n;

assert (n >= 0);

var f : int := 1;
for i in 1..n do
    f := f * i;
end for;

print "n! = ";
print f;
print "\n";
