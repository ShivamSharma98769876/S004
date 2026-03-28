/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Smaller production image when served behind nginx in the combined container / Azure.
  output: "standalone",
  async rewrites() {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
