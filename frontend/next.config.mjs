/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Dev: allow http://127.0.0.1:3000 vs localhost (Next 14+ /_next/* cross-origin warning).
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // Smaller production image when served behind nginx in the combined container / Azure.
  output: "standalone",
  // Dev rewrites proxy /api/* to FastAPI. Default timeout is short; recommendations can run ensure_recommendations (Kite + chains) longer.
  experimental: {
    proxyTimeout: 180_000,
  },
  async rewrites() {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
