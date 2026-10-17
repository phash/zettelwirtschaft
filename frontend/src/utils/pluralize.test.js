import { describe, it, expect } from 'vitest'
import { pluralize } from './pluralize'

describe('pluralize', () => {
  it('nutzt Singular nur bei genau 1', () => {
    expect(pluralize(1, 'Hinweis', 'Hinweise')).toBe('1 Hinweis')
    expect(pluralize(0, 'Hinweis', 'Hinweise')).toBe('0 Hinweise')
    expect(pluralize(2, 'Tag', 'Tage')).toBe('2 Tage')
  })

  it('behandelt fehlende Werte als 0', () => {
    expect(pluralize(undefined, 'Beleg', 'Belege')).toBe('0 Belege')
  })
})
