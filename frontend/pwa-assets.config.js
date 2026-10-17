// PWA-Icons (192/512, maskable, apple-touch-icon, favicon.ico) aus dem Logo
// erzeugen: `npm run generate-pwa-assets`. Die erzeugten PNGs liegen
// eingecheckt in public/ — der Docker-/Native-Build braucht so weder sharp
// noch den Generator (der Generator laeuft per npx, ist keine Dependency;
// deshalb hier ein reines Objekt ohne Import aus '@vite-pwa/assets-generator').
// Entspricht dem Preset "minimal-2023", nur Maskable-/Apple-Icon mit der
// Markenfarbe hinterlegt statt Weiss. Nach einer Logo-Aenderung neu generieren.
const BRAND = '#2563eb'

export default {
  headLinkOptions: { preset: '2023' },
  preset: {
    transparent: { sizes: [64, 192, 512], favicons: [[48, 'favicon.ico']] },
    maskable: { sizes: [512], padding: 0.3, resizeOptions: { background: BRAND } },
    apple: { sizes: [180], padding: 0.3, resizeOptions: { background: BRAND } },
  },
  images: ['public/favicon.svg'],
}
