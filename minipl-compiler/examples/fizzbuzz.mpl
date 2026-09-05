// Классический FizzBuzz: цикл for, вложенные if и сравнение строк.
var limit : int := 15;

for i in 1..limit do
    var out : string := "";

    if i % 3 = 0 do
        out := out + "Fizz";
    end if;

    if i % 5 = 0 do
        out := out + "Buzz";
    end if;

    if out = "" do
        print i;
    else
        print out;
    end if;

    print "\n";
end for;
