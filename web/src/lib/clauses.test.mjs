import assert from "node:assert/strict";
import { test } from "node:test";

import { splitClauses, toCnClause } from "./clauses.mjs";

test("条款号兼容空格、顿号、冒号和阿拉伯数字", () => {
  const text = "采购合同\n甲方：华信\n第一条 标的\n内容一\n第二条、价款\n第3条：检验\n第十二条\n";
  const cs = splitClauses(text);
  assert.deepEqual(cs.map((c) => c.no), [null, "第一条", "第二条", "第三条", "第十二条"]);
  assert.deepEqual(cs[1].lines, ["第一条 标的", "内容一"]);
});

test("方括号标题也算条款", () => {
  assert.deepEqual(splitClauses("第一条【价款】\n单价\n").map((c) => c.no), ["第一条"]);
});

test("正文里提到的条款号不切分", () => {
  const cs = splitClauses("第一条 违约\n依照第二条约定执行\n第二条约定的期限同上\n");
  assert.equal(cs.length, 1);
});

test("阿拉伯条号转中文", () => {
  assert.equal(toCnClause("第12条"), "第十二条");
  assert.equal(toCnClause("第20条"), "第二十条");
  assert.equal(toCnClause("第五条"), "第五条");
});
