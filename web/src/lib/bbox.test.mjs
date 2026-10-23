import assert from "node:assert/strict";
import { test } from "node:test";

import { bboxToViewport, fitScale } from "./bbox.mjs";

test("bboxToViewport scales top-left PDF points", () => {
  assert.deepEqual(bboxToViewport([10, 20, 110, 40], 2, 0), { left: 20, top: 40, width: 200, height: 40 });
});

test("bboxToViewport pads around the box", () => {
  const v = bboxToViewport([10, 20, 110, 40], 1, 2);
  assert.equal(v.left, 8);
  assert.equal(v.height, 24);
});

test("fitScale fits page width into container", () => {
  assert.equal(fitScale(437, 874), 2);
  assert.equal(fitScale(0, 800), 1);
});
