// Runs rag_static.js in Node for tests/test_asktab.py. Reads {data, questions, texts} as JSON on stdin and prints the engine's results.
const { createEngine } = require("../rag_static.js");
let input = "";
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  const { data, questions, texts } = JSON.parse(input);
  const engine = createEngine(data);
  process.stdout.write(JSON.stringify({
    answers: questions.map((q) => engine.ask(q)),
    hits: questions.map((q) => engine.search(q).map((h) => ({ title: h.title, score: h.score }))),
    terms: texts.map((t) => engine.terms(t)),
    sentences: texts.map((t) => engine.sentences(t)),
  }));
});
