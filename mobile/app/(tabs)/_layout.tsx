import { Tabs } from 'expo-router';
import { Platform } from 'react-native';
import { useTheme } from '@/ThemeContext';

const isKaroo = Platform.OS === 'android' && (Platform.Version as number) < 29;

export default function TabLayout() {
  const theme = useTheme();
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarStyle: { backgroundColor: '#18181b', borderTopColor: '#27272a' },
        tabBarActiveTintColor: theme.accent,
        tabBarInactiveTintColor: '#71717a',
      }}
    >
      <Tabs.Screen
        name="index"
        options={{ title: 'Feed', tabBarIcon: ({ color }) => <TabIcon label="⬡" color={color} /> }}
      />
      <Tabs.Screen
        name="import"
        options={{ title: 'Import', tabBarIcon: ({ color }) => <TabIcon label="↑" color={color} /> }}
      />
      <Tabs.Screen
        name="search"
        options={{
          title: 'Search',
          tabBarIcon: ({ color }) => <TabIcon label="⌕" color={color} />,
          href: isKaroo ? null : '/search',
        }}
      />
      <Tabs.Screen
        name="settings"
        options={{ title: 'Settings', tabBarIcon: ({ color }) => <TabIcon label="⚙" color={color} /> }}
      />
    </Tabs>
  );
}

function TabIcon({ label, color }: { label: string; color: string }) {
  const { Text } = require('react-native');
  return <Text style={{ color, fontSize: 18 }}>{label}</Text>;
}
