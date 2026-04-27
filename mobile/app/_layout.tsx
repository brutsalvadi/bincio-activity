import { Stack } from 'expo-router';
import { SQLiteProvider } from 'expo-sqlite';
import { StatusBar } from 'expo-status-bar';
import { migrateDb } from '@/db';
import { ThemeProvider } from '@/ThemeContext';

export default function RootLayout() {
  return (
    <SQLiteProvider databaseName="bincio.db" onInit={migrateDb}>
      <ThemeProvider>
        <StatusBar style="light" />
        <Stack screenOptions={{ headerShown: false }} />
      </ThemeProvider>
    </SQLiteProvider>
  );
}
