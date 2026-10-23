export function bboxToViewport(
  bbox: [number, number, number, number],
  scale: number,
  pad?: number,
): { left: number; top: number; width: number; height: number };
export function fitScale(pageWidth: number, containerWidth: number): number;
