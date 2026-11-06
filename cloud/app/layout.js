export const metadata = {
  title: 'Stock · Enregistreur de produits',
  description: 'Enregistreur de produits par code-barres pour boutiques'
};

export default function RootLayout({children}) {
  return (
    <html lang="fr">
      <body>{children}</body>
    </html>
  );
}
