/** @type {import('next').NextConfig} */
const nextConfig = {
  // "standalone" bundles a self-contained server into .next/standalone, which
  // is what the web image runs. The board used to be a static export rebuilt
  // by a push of data/today.json; it now reads the extractor's volume on each
  // request, so it needs a server runtime and no longer needs a deploy to show
  // a new scan.
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
