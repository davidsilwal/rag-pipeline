import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["david-contabo-wo", "100.72.153.12", "localhost", "127.0.0.1"],
  // Optional: proxy API calls to avoid CORS in same-host deployments
  // Uncomment if frontend and API run on the same VPS:
  // async rewrites() {
  //   return [
  //     {
  //       source: "/api/:path*",
  //       destination: "http://control-api:8000/api/:path*",
  //     },
  //   ];
  // },
};

export default nextConfig;
