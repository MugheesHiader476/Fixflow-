"""Safe, deterministic difficult-content inputs shared by tests and measured experiments."""

import json


def difficult_corpus() -> list[tuple[str, str]]:
    words = "authorized technical evidence café 日本語 🚀 "
    statements = "".join(f"    value_{i} = {i}\n" for i in range(220))
    braces = "".join(f"let value{i} = {i};\n" for i in range(220))
    nested = '"DEEP_END"'
    for _ in range(100):
        nested = '{"level":' + nested + "}"
    xml = "<level>" * 100 + words * 200 + "</level>" * 100
    headers = "| ID | Notes |\n| --- | --- |\n"
    return [
        ("sentence.txt", words * 350 + "END."),
        ("clauses.txt", "; ".join(f"clause {i} states {words}" for i in range(300)) + "."),
        ("paragraph.md", "# Evidence\n\n" + words * 300),
        ("function.py", "def evidence():\n" + statements + "    return value_219\n"),
        ("statement.py", 'def evidence():\n    value = "' + "x" * 14000 + '"\n    return value\n'),
        ("function.js", "function evidence() {\n" + braces + "return value219;\n}"),
        ("object.js", "const evidence = {" + ",".join(f'key{i}: "value{i}"' for i in range(600)) + "};"),
        ("class.ts", "export class Evidence {\n  evaluate(): number {\n" + braces + "return value219;\n}}"),
        (
            "class.java",
            "class Evidence {\n int evaluate() {\n" + braces.replace("let ", "int ") + "return value219;\n}}",
        ),
        ("function.c", "int evidence(void) {\n" + braces.replace("let ", "int ") + "return value219;\n}"),
        (
            "function.cpp",
            "class Evidence { public: int evaluate() {\n" + braces.replace("let ", "int ") + "return value219;\n}};",
        ),
        (
            "function.go",
            "package main\nfunc evidence() int {\n"
            + braces.replace("let ", "var ").replace(" = ", " = ")
            + "return value219\n}",
        ),
        ("function.rs", "fn evidence() -> i32 {\n" + braces + "value219\n}"),
        ("nested.json", nested),
        ("scalar.json", json.dumps({"description": words * 350}, ensure_ascii=False)),
        ("integer.json", '{"number":' + "9" * 10000 + "}"),
        ("exponent.json", '{"number":1e10000}'),
        ("precise.json", '{"number":1.000000000000000000000000000000001}'),
        ("nested.yaml", "\n".join("  " * i + "level:" for i in range(100)) + "\n" + "  " * 100 + "leaf: DEEP_END\n"),
        ("scalar.yaml", "description: |\n  " + words * 350 + "\n"),
        ("integer.yaml", "number: " + "9" * 10000),
        ("exponent.yaml", "number: 1.0e+10000"),
        ("nested.xml", xml),
        ("text.xml", '<article version="2">START<p>' + words * 350 + "</p>END</article>"),
        ("table.md", headers + "\n".join(f"| row{i} | evidence{i} |" for i in range(250))),
        ("cell.md", headers + "| row1 | " + words * 300 + " |\n| row2 | END |"),
        ("row.csv", ",".join(f"Column{i}" for i in range(150)) + "\n" + ",".join(f"Value{i}" for i in range(150))),
        ("header.csv", "Identifier," + "H" * 3000 + "\nrow1,evidence"),
        ("unicode.txt", "日本語🚀café" * 1600),
        ("minified.js", "const giant = '" + "x" * 18000 + "';"),
        ("query.sql", "SELECT " + ", ".join(f"value{i}" for i in range(800)) + " FROM evidence;"),
        ("call.js", "inspect(" + ",".join(f'"argument{i}"' for i in range(900)) + ");"),
        ("chain.js", "const result = evidence" + ".inspect()" * 1300 + ";"),
    ]
