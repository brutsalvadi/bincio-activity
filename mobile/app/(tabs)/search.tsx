import { useState } from 'react';
import { FlatList, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { PAGE_SIZE, useActivityYears, useFilteredActivities, useFilteredCount, type ActivityFilter } from '@/db/queries';
import { ActivityCard } from '@/components/ActivityCard';
import { useTheme } from '@/ThemeContext';

type SortKey = 'date' | 'distance' | 'elevation';

const SPORTS = [
  { value: '',         label: 'All' },
  { value: 'cycling',  label: '🚴 Cycling' },
  { value: 'running',  label: '🏃 Running' },
  { value: 'hiking',   label: '🥾 Hiking' },
  { value: 'swimming', label: '🏊 Swimming' },
  { value: 'walking',  label: '🚶 Walking' },
];

const DATE_PRESETS = [
  { value: 'all',  label: 'All time' },
  { value: '7d',   label: '7 days' },
  { value: '30d',  label: '30 days' },
  { value: '6mo',  label: '6 months' },
];

const SORTS: { value: SortKey; label: string }[] = [
  { value: 'date',      label: 'Newest' },
  { value: 'distance',  label: 'Distance' },
  { value: 'elevation', label: 'Elevation' },
];

function computeDateRange(preset: string): { dateFrom: string; dateTo: string } {
  if (preset === 'all') return { dateFrom: '', dateTo: '' };
  if (/^\d{4}$/.test(preset)) {
    const y = parseInt(preset, 10);
    return { dateFrom: `${y}-01-01T000000Z`, dateTo: `${y + 1}-01-01T000000Z` };
  }
  const pad = (n: number) => String(n).padStart(2, '0');
  const now = new Date();
  let d: Date;
  if      (preset === '7d')  d = new Date(now.getTime() - 7  * 86_400_000);
  else if (preset === '30d') d = new Date(now.getTime() - 30 * 86_400_000);
  else                       { d = new Date(now); d.setMonth(d.getMonth() - 6); }
  return { dateFrom: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T000000Z`, dateTo: '' };
}

export default function SearchScreen() {
  const theme = useTheme();
  const [sport,   setSport]   = useState('');
  const [datePre, setDatePre] = useState('all');
  const [sort,    setSort]    = useState<SortKey>('date');
  const [limit,   setLimit]   = useState(PAGE_SIZE);

  const years = useActivityYears();
  const dateOptions = [...DATE_PRESETS, ...years.map(y => ({ value: y, label: y }))];

  const { dateFrom, dateTo } = computeDateRange(datePre);
  const filter: ActivityFilter = { sport, dateFrom, dateTo, sort };
  const activities = useFilteredActivities(filter, limit);
  const total      = useFilteredCount(filter);
  const hasMore    = activities.length < total;

  return (
    <View style={styles.container}>
      <View style={styles.headerRow}>
        <Text style={styles.header}>Filter</Text>
        {total > 0 && <Text style={styles.count}>{total} activities</Text>}
      </View>

      <ScrollView horizontal showsHorizontalScrollIndicator={false}
        style={styles.pillScroll} contentContainerStyle={styles.pillRow}>
        {SPORTS.map(s => (
          <Pill key={s.value} label={s.label} active={sport === s.value} accent={theme.accent}
            onPress={() => { setSport(s.value); setLimit(PAGE_SIZE); }} />
        ))}
      </ScrollView>

      <ScrollView horizontal showsHorizontalScrollIndicator={false}
        style={styles.pillScroll} contentContainerStyle={styles.pillRow}>
        {dateOptions.map(d => (
          <Pill key={d.value} label={d.label} active={datePre === d.value} accent={theme.accent}
            onPress={() => { setDatePre(d.value); setLimit(PAGE_SIZE); }} />
        ))}
      </ScrollView>

      <View style={styles.sortRow}>
        {SORTS.map(s => (
          <Pressable key={s.value}
            style={[styles.sortBtn, sort === s.value && { borderBottomColor: theme.accent, borderBottomWidth: 2 }]}
            onPress={() => { setSort(s.value); setLimit(PAGE_SIZE); }}>
            <Text style={[styles.sortText, sort === s.value && { color: theme.accent }]}>{s.label}</Text>
          </Pressable>
        ))}
      </View>

      {activities.length === 0 ? (
        <View style={styles.empty}>
          <Text style={styles.emptyText}>No activities match</Text>
        </View>
      ) : (
        <FlatList
          style={{ flex: 1 }}
          data={activities}
          keyExtractor={a => a.id}
          renderItem={({ item }) => (
            <ActivityCard activity={item} selecting={false} checked={false}
              onToggleSelect={() => {}} onLongPress={() => {}} />
          )}
          contentContainerStyle={styles.list}
          onEndReached={() => { if (hasMore) setLimit(l => l + PAGE_SIZE); }}
          onEndReachedThreshold={0.3}
        />
      )}
    </View>
  );
}

function Pill({ label, active, accent, onPress }: {
  label: string; active: boolean; accent: string; onPress: () => void;
}) {
  return (
    <Pressable
      style={[styles.pill, active && { backgroundColor: accent + '33', borderColor: accent }]}
      onPress={onPress}
    >
      <Text style={[styles.pillText, active && { color: accent }]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container:  { flex: 1, backgroundColor: '#09090b' },
  headerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingTop: 60, paddingBottom: 12,
  },
  header:     { color: '#fff', fontSize: 22, fontWeight: '700' },
  count:      { color: '#71717a', fontSize: 13 },
  pillScroll: { flexGrow: 0, flexShrink: 0 },
  pillRow:    { flexDirection: 'row', gap: 8, paddingHorizontal: 16, paddingBottom: 10 },
  pill: {
    borderRadius: 20, borderWidth: 1, borderColor: '#3f3f46',
    paddingHorizontal: 14, paddingVertical: 7,
  },
  pillText:   { color: '#a1a1aa', fontSize: 13, fontWeight: '500' },
  sortRow:    { flexDirection: 'row', paddingHorizontal: 16, marginBottom: 4 },
  sortBtn:    { marginRight: 24, paddingBottom: 8, borderBottomWidth: 2, borderBottomColor: 'transparent' },
  sortText:   { color: '#71717a', fontSize: 13, fontWeight: '600' },
  list:       { padding: 16, gap: 12, paddingBottom: 80 },
  empty:      { flex: 1, alignItems: 'center', justifyContent: 'center' },
  emptyText:  { color: '#52525b', fontSize: 15 },
});
