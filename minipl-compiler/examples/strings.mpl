// Работа со строками, логическими значениями и сокращённым вычислением.
var name : string;
print "Как вас зовут? ";
read name;

var greeting : string := "Привет, " + name + "!";
print greeting;
print "\n";

var short : bool := name < "Мария";
print "Имя лексикографически меньше \"Мария\": ";
print short;
print "\n";

// правая часть не вычисляется, если левая уже определяет результат
var safe : bool := false & (10 / 0 = 1);
print "сокращённое вычисление сработало: ";
print !safe;
print "\n";
