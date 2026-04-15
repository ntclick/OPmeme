/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  webpack: (config) => {
    // MetaMask SDK tries to import react-native-async-storage which doesn't
    // exist in web builds. Stub it out so the build doesn't warn.
    config.resolve.fallback = {
      ...config.resolve.fallback,
      "@react-native-async-storage/async-storage": false,
    };
    return config;
  },
};

module.exports = nextConfig;
