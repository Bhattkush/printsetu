import tailwindcss from '@tailwindcss/vite';
const proxy = { '/api': { target: process.env.PRINTSETU_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: false, timeout: 120000, proxyTimeout: 120000 } };
export default {
  plugins: [tailwindcss()],
  server: { host: '0.0.0.0', port: 5173, strictPort: true, proxy },
  preview: { host: '0.0.0.0', proxy },
};
