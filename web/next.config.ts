import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export", // 静态导出到 web/out，由 `cra serve`（FastAPI）托管，一条命令启动
  devIndicators: false,
  images: { unoptimized: true },
  trailingSlash: true, // 导出成 eval/index.html，FastAPI 静态托管能直接按目录找到
};

export default nextConfig;
