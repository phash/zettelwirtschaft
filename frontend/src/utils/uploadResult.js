// Gemeinsame Auswertung der MultiUploadResponse ({ uploaded, rejected }) fuer
// UploadView, ScanView und den Dashboard-Drop: jede abgelehnte Datei bekommt
// einen eigenen Fehler-Toast, die Erfolgsmeldung zaehlt nur echte Uploads.
export function reportUploadResult(result, notify, { noun = 'Datei(en)' } = {}) {
  const uploaded = result?.uploaded || []
  const rejected = result?.rejected || []
  if (uploaded.length) {
    notify.success(`${uploaded.length} ${noun} hochgeladen.`)
  }
  for (const r of rejected) {
    notify.error(`${r.filename}: ${r.error}`)
  }
  return { uploaded, rejected }
}
