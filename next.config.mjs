/** @type {import('next').NextConfig} */
const nextConfig = {
  // The board is one static page built from a committed JSON file: no server
  // runtime is needed, and a static export is the cheapest thing Vercel can
  // serve. A push of data/today.json rebuilds it.
  output: "export",
  reactStrictMode: true,
};

export default nextConfig;
