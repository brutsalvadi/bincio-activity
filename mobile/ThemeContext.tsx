import { createContext, useContext, useEffect, useState } from 'react';
import { useSQLiteContext } from 'expo-sqlite';
import { getSetting, setSetting } from '@/db/queries';
import { autoKey, PALETTES, type PaletteKey, type Theme } from '@/theme';

type ThemeCtx = {
  theme: Theme;
  paletteKey: PaletteKey;
  setPaletteOverride: (key: PaletteKey) => void;
};

const ThemeContext = createContext<ThemeCtx>({
  theme: PALETTES.default,
  paletteKey: 'auto',
  setPaletteOverride: () => {},
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const db = useSQLiteContext();
  const [paletteKey, setPaletteKey] = useState<PaletteKey>('auto');

  useEffect(() => {
    getSetting(db, 'palette_override').then(val => {
      if (val) setPaletteKey(val as PaletteKey);
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function setPaletteOverride(key: PaletteKey) {
    setPaletteKey(key);
    setSetting(db, 'palette_override', key);
  }

  const resolved = paletteKey === 'auto' ? autoKey() : paletteKey;
  const theme = PALETTES[resolved as keyof typeof PALETTES] ?? PALETTES.default;

  return (
    <ThemeContext.Provider value={{ theme, paletteKey, setPaletteOverride }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): Theme {
  return useContext(ThemeContext).theme;
}

export function usePaletteControl(): Pick<ThemeCtx, 'paletteKey' | 'setPaletteOverride'> {
  const { paletteKey, setPaletteOverride } = useContext(ThemeContext);
  return { paletteKey, setPaletteOverride };
}
