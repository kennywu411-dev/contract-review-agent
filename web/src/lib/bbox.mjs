// 入库时 bbox 已统一为左上原点的 PDF 点坐标（docling → to_top_left_origin），
// pdf.js 视口同样是左上原点，所以只需乘以缩放比例；留 2pt 边距让高亮框不压字。
export function bboxToViewport(bbox, scale, pad = 2) {
  const [l, t, r, b] = bbox;
  return {
    left: (l - pad) * scale,
    top: (t - pad) * scale,
    width: (r - l + 2 * pad) * scale,
    height: (b - t + 2 * pad) * scale,
  };
}

// 宽度适配：让页面刚好放进容器
export function fitScale(pageWidth, containerWidth) {
  return pageWidth > 0 ? containerWidth / pageWidth : 1;
}
