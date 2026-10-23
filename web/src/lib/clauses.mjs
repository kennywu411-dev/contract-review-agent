// 合同条款切分（前端高亮用），条款号写法与后端 review_agent.normalize_clause 对齐。

const CN = "零一二三四五六七八九";
// 行首的"第X条"，后面跟空白、顿号、冒号、点号或行尾才算条款标题（"第二条约定"这类正文引用不算）
const HEAD = /^(第[零一二三四五六七八九十百\d]+条)(?=[\s、：:．.]|$)/;

/** "第12条" → "第十二条"；已是中文的原样返回。 */
export function toCnClause(no) {
  const m = no.match(/^第(\d+)条$/);
  if (!m) return no;
  const n = Number(m[1]);
  const cn =
    n < 10 ? CN[n] : n < 20 ? "十" + (n % 10 ? CN[n % 10] : "") : CN[Math.floor(n / 10)] + "十" + (n % 10 ? CN[n % 10] : "");
  return `第${cn}条`;
}

/** 把合同切成"首部 + 各条"；首部的 no 为 null，没有内容的段丢掉。 */
export function splitClauses(text) {
  const out = [{ no: null, lines: [] }];
  for (const line of text.split("\n")) {
    const m = line.match(HEAD);
    if (m) out.push({ no: toCnClause(m[1]), lines: [line] });
    else out[out.length - 1].lines.push(line);
  }
  return out.filter((c) => c.lines.some((l) => l.trim()));
}
