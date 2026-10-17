// Erlaubte Upload-Formate — Spiegel von backend ALLOWED_FILE_TYPES + MAX_UPLOAD_SIZE_MB
// (app/config.py, Defaults) inkl. Alias .tif -> tiff (core/file_utils.normalize_file_type).
// Bei einer Aenderung im Backend hier mitziehen. Einzige Quelle fuer accept-Listen,
// Format-Hinweise und die Client-Vorpruefung in Upload, Scan und Dashboard.

export const ALLOWED_EXTENSIONS = ['pdf', 'jpg', 'jpeg', 'png', 'tiff', 'tif', 'bmp']

// Pro Datei (Backend-Pruefung) und zugleich nginx client_max_body_size — darum
// laedt uploadDocuments() jede Datei in einem eigenen Request hoch.
export const MAX_UPLOAD_SIZE_MB = 50

// Fuer <input type="file" accept="...">: Endungen plus MIME-Typen, damit
// mobile Browser (Kamera/Galerie) die passenden Quellen anbieten.
export const FILE_ACCEPT = [
  ...ALLOWED_EXTENSIONS.map((ext) => `.${ext}`),
  'application/pdf',
  'image/jpeg',
  'image/png',
  'image/tiff',
  'image/bmp',
].join(',')

// Anzeige-Text fuer Hinweise ("Erlaubt: ...").
export const FILE_TYPES_LABEL = 'PDF, JPG, PNG, TIFF, BMP'

export function fileExtension(name) {
  const match = /\.([^./\\]+)$/.exec(String(name || ''))
  return match ? match[1].toLowerCase() : ''
}

// Client-Vorpruefung (das Backend prueft zusaetzlich Magic-Bytes und Groesse).
export function isAllowedFile(file) {
  return ALLOWED_EXTENSIONS.includes(fileExtension(file?.name))
}
