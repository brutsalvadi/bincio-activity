import { useRouter } from 'expo-router';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { ActivitySummary } from '@/db/queries';
import { useTheme } from '@/ThemeContext';

export function ActivityCard({
  activity,
  selecting,
  checked,
  onToggleSelect,
  onLongPress,
}: {
  activity: ActivitySummary;
  selecting: boolean;
  checked: boolean;
  onToggleSelect: () => void;
  onLongPress: () => void;
}) {
  const router = useRouter();
  const theme = useTheme();
  const km   = activity.distance_m != null ? (activity.distance_m / 1000).toFixed(1) : null;
  const elev = activity.elevation_gain_m != null ? Math.round(activity.elevation_gain_m) : null;
  const date = new Date(activity.started_at).toLocaleDateString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric',
  });

  function handlePress() {
    if (selecting) onToggleSelect();
    else router.push(`/activity/${activity.id}`);
  }

  return (
    <Pressable
      style={[styles.card, checked && { borderColor: theme.accent }]}
      onPress={handlePress}
      onLongPress={onLongPress}
    >
      <View style={styles.cardTop}>
        <View style={styles.cardLeft}>
          {selecting && (
            <View style={[styles.checkbox, checked && { backgroundColor: theme.accent, borderColor: theme.accent }]}>
              {checked && <Text style={styles.checkmark}>✓</Text>}
            </View>
          )}
          <Text style={styles.sportIcon}>{sportIcon(activity.sport)}</Text>
        </View>
        <View style={styles.cardMeta}>
          <Text style={styles.cardDate}>{date}</Text>
          {activity.origin === 'remote'
            ? <Text style={[styles.remoteBadge, { color: theme.accent, borderColor: theme.accent }]}>cloud</Text>
            : !activity.synced_at && <Text style={styles.localBadge}>local</Text>
          }
        </View>
      </View>
      <Text style={styles.cardTitle} numberOfLines={1}>{activity.user_title ?? activity.title}</Text>
      <View style={styles.cardStats}>
        {km   && <Stat label="km" value={km} />}
        {elev != null && <Stat label="m↑" value={String(elev)} />}
      </View>
    </Pressable>
  );
}

export function Stat({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </View>
  );
}

export function sportIcon(sport: string): string {
  const icons: Record<string, string> = {
    cycling: '🚴', running: '🏃', hiking: '🥾', swimming: '🏊', walking: '🚶',
  };
  return icons[sport] ?? '🏅';
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#18181b', borderRadius: 12,
    padding: 16, borderWidth: 1, borderColor: '#27272a',
  },
  cardTop:  { flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 },
  cardLeft: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  sportIcon: { fontSize: 20 },
  cardMeta: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  cardDate: { color: '#71717a', fontSize: 12 },
  remoteBadge: {
    fontSize: 10, borderWidth: 1,
    borderRadius: 4, paddingHorizontal: 4,
  },
  localBadge: {
    color: '#a1a1aa', fontSize: 10, borderWidth: 1,
    borderColor: '#3f3f46', borderRadius: 4, paddingHorizontal: 4,
  },
  cardTitle:  { color: '#f4f4f5', fontSize: 15, fontWeight: '600', marginBottom: 10 },
  cardStats:  { flexDirection: 'row', gap: 16 },
  stat:       { flexDirection: 'row', alignItems: 'baseline', gap: 3 },
  statValue:  { color: '#f4f4f5', fontSize: 16, fontWeight: '600' },
  statLabel:  { color: '#71717a', fontSize: 12 },
  checkbox: {
    width: 20, height: 20, borderRadius: 4, borderWidth: 1.5,
    borderColor: '#52525b', alignItems: 'center', justifyContent: 'center',
  },
  checkmark: { color: '#fff', fontSize: 12, fontWeight: '700' },
});
