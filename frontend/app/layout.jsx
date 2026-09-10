import "./globals.css";

export const metadata = {
  title: "HYPE Copilot",
  description: "¿Cuál es la mejor oportunidad de HYPE ahora?",
};

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
