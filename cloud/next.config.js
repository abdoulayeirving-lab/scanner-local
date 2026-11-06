/** @type {import('next').NextConfig} */
module.exports = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {source: '/', destination: '/index.html'},
      {source: '/scan', destination: '/scan.html'},
      {source: '/login', destination: '/login.html'}
    ];
  },
  async headers() {
    return [
      {
        source: '/sw.js',
        headers: [
          {key: 'Service-Worker-Allowed', value: '/'},
          {key: 'Cache-Control', value: 'no-cache'}
        ]
      }
    ];
  }
};
