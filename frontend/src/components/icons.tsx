/** Jeu d'icônes SVG minimalistes (stroke 1.7). */
const PATHS: Record<string, string> = {
  dashboard: "M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z",
  suivi: "M9 5h11M9 12h11M9 19h11M4 5h.01M4 12h.01M4 19h.01",
  missions: "M9 20l-6-3V4l6 3m0 13l6-3m-6 3V7m6 10l6 3V7l-6-3m0 13V4M9 7L3 4m18 0l-6 3",
  infractions: "M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  alertes: "M18 8a6 6 0 00-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0",
  historique: "M3 12a9 9 0 109-9 9.5 9.5 0 00-6.7 2.8L3 8m0-5v5h5M12 7v5l4 2",
  conducteurs: "M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2M9 11a4 4 0 100-8 4 4 0 000 8M23 21v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8",
  vehicules: "M1 3h15v13H1zM16 8h4l3 3v5h-7V8M5.5 21a2.5 2.5 0 100-5 2.5 2.5 0 000 5M18.5 21a2.5 2.5 0 100-5 2.5 2.5 0 000 5",
  parametres: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.9 2.9l-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.2a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.9-2.9l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.2A1.7 1.7 0 004.7 8a1.7 1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.9-2.9l.1.1a1.7 1.7 0 001.9.3h0a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.2a1.7 1.7 0 001 1.5 1.7 1.7 0 001.9-.3l.1-.1a2 2 0 112.9 2.9l-.1.1a1.7 1.7 0 00-.3 1.9v0a1.7 1.7 0 001.5 1h.2a2 2 0 110 4h-.2a1.7 1.7 0 00-1.5 1z",
  recherche: "M21 21l-4.3-4.3M11 19a8 8 0 100-16 8 8 0 000 16z",
  soleil: "M12 17a5 5 0 100-10 5 5 0 000 10zM12 1v2m0 18v2M4.2 4.2l1.4 1.4m12.8 12.8l1.4 1.4M1 12h2m18 0h2M4.2 19.8l1.4-1.4m12.8-12.8l1.4-1.4",
  lune: "M21 12.8A9 9 0 1111.2 3 7 7 0 0021 12.8z",
  deconnexion: "M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9",
  fermer: "M18 6L6 18M6 6l12 12",
  telecharger: "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3",
  chevron: "M6 9l6 6 6-6",
  localisation: "M21 10c0 7-9 13-9 13S3 17 3 10a9 9 0 1118 0zM12 13a3 3 0 100-6 3 3 0 000 6z",
  carburant: "M12 2.7s6.5 7 6.5 11.3a6.5 6.5 0 11-13 0C5.5 9.7 12 2.7 12 2.7z",
  horloge: "M12 22a10 10 0 100-20 10 10 0 000 20zM12 6v6l4 2",
  vitesse: "M12 14l3.5-3.5M20.2 18a9 9 0 10-16.4 0",
  plus: "M12 5v14M5 12h14",
  verifier: "M20 6L9 17l-5-5",
  debuter: "M12 2a10 10 0 1 0 10 10M12 6v6l4 2",
  tableau: "M3 3h18v18H3zM3 9h18M3 15h18M9 3v18",
  calendrier: "M8 2v4M16 2v4M3 8h18M5 4h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V6a2 2 0 012-2z",
  alerte: "M12 9v4m0 4h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
  rafraichir: "M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15",
  copier: "M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3",
};

export default function Icon({ nom, className = "w-4 h-4" }: { nom: string; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d={PATHS[nom] || PATHS.dashboard} />
    </svg>
  );
}
