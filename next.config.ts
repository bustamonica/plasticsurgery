import type { NextConfig } from "next";

// Note: App Router route handlers have no built-in request body size limit;
// the upload cap for /api/generate is enforced inside the route itself.
const nextConfig: NextConfig = {};

export default nextConfig;
