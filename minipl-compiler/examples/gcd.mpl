/*
 * Наибольший общий делитель по алгоритму Евклида.
 * Демонстрирует while, if/else и оператор остатка %.
 */
var a : int;
var b : int;

print "a = ";
read a;
print "b = ";
read b;

assert (a > 0 & b > 0);

while b <> 0 do
    var t : int := b;
    b := a % b;
    a := t;
end while;

print "НОД = ";
print a;
print "\n";
