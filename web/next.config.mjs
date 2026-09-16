/** @type {import('next').NextConfig} */
export default {
  // Bundles a self-contained server into .next/standalone, which is what the
  // web image runs — no node_modules tree, no source, no toolchain.
  output: "standalone",
  reactStrictMode: true,
};
