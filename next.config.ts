import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Uploaded photos are sent to the generate API as base64 JSON, so allow
  // larger request bodies for server actions / API routes.
  experimental: {
    serverActions: {
      bodySizeLimit: "12mb",
    },
  },
};

export default nextConfig;
