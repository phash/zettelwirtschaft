import { describe, it, expect } from 'vitest'
import { FILE_ACCEPT, isAllowedFile, fileExtension } from './fileTypes'

describe('fileTypes', () => {
  it('erlaubt die Backend-Formate inkl. .tif-Alias, case-insensitiv', () => {
    for (const name of ['a.pdf', 'b.JPG', 'c.jpeg', 'd.png', 'e.tiff', 'f.tif', 'g.bmp']) {
      expect(isAllowedFile({ name })).toBe(true)
    }
  })

  it('lehnt andere Formate und Dateien ohne Endung ab', () => {
    for (const name of ['a.docx', 'b.heic', 'pdf', '', 'archiv.pdf.exe']) {
      expect(isAllowedFile({ name })).toBe(false)
    }
    expect(isAllowedFile(null)).toBe(false)
  })

  it('accept-Liste enthaelt Endungen und MIME-Typen', () => {
    expect(FILE_ACCEPT).toContain('.pdf')
    expect(FILE_ACCEPT).toContain('.tif')
    expect(FILE_ACCEPT).toContain('image/jpeg')
    expect(fileExtension('Scan.Seite1.PNG')).toBe('png')
  })
})
