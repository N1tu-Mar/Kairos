/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The FastAPI URL is server-only on purpose: no NEXT_PUBLIC_ prefix, so it
  // never ships to the browser and the browser never talks to FastAPI direct.
  // Client interactions go through the thin Route Handler proxy in src/app/api.
  eslint: { dirs: ["src"] },
};

export default nextConfig;
