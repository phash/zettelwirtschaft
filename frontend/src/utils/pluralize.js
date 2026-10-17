// Zahl + Singular/Plural ("1 Hinweis", "2 Hinweise"). Fuer UI-Texte, die eine
// Anzahl nennen — vermeidet "1 Tage" / "1 Hinweise".
export function pluralize(count, singular, plural) {
  const n = Number(count) || 0
  return `${n} ${n === 1 ? singular : plural}`
}
