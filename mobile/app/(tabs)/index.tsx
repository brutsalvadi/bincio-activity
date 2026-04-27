import * as FileSystem from 'expo-file-system';
import { useFocusEffect } from 'expo-router';
import { useSQLiteContext } from 'expo-sqlite';
import { useCallback, useState } from 'react';
import { Alert, FlatList, Pressable, RefreshControl, StyleSheet, Text, TextInput, View } from 'react-native';
import { deleteActivities, useActivities, useActivityCount, PAGE_SIZE } from '@/db/queries';
import { downloadFeed, uploadFeed } from '@/db/sync';
import { useTheme } from '@/ThemeContext';
import { ActivityCard } from '@/components/ActivityCard';

export default function FeedScreen() {
  const db = useSQLiteContext();
  const theme = useTheme();
  const [refreshKey,   setRefreshKey]   = useState(0);
  const [searchQuery,  setSearchQuery]  = useState('');
  const [limit,        setLimit]        = useState(PAGE_SIZE);
  const activities = useActivities(searchQuery, limit);
  const totalCount = useActivityCount(searchQuery);
  const hasMore    = activities.length < totalCount;
  const [downloading, setDownloading] = useState(false);
  const [uploading,   setUploading]   = useState(false);
  const [statusMsg,   setStatusMsg]   = useState<{ ok: boolean; text: string } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const selecting = selected.size > 0;

  // Auto-refresh the local list whenever the tab comes into focus.
  // SQLite getAllSync is sub-millisecond — no network, no lag.
  useFocusEffect(useCallback(() => {
    setRefreshKey(k => k + 1);
  }, []));

  function showMsg(ok: boolean, text: string) {
    setStatusMsg({ ok, text });
    setTimeout(() => setStatusMsg(null), 3500);
  }

  const doDownload = useCallback(async () => {
    setDownloading(true);
    setStatusMsg(null);
    const result = await downloadFeed(db);
    setDownloading(false);
    setRefreshKey(k => k + 1);
    if (result.error) {
      showMsg(false, result.error);
    } else if (result.total === 0) {
      showMsg(true, 'No activities on instance');
    } else if (result.synced === 0 && !result.fetched) {
      showMsg(true, `Up to date (${result.total} activities)`);
    } else {
      const parts = [];
      if (result.synced > 0) parts.push(`${result.synced} new`);
      if (result.fetched)    parts.push(`${result.fetched} full dataset${result.fetched === 1 ? '' : 's'}`);
      showMsg(true, `Downloaded: ${parts.join(', ')} (${result.total} total)`);
    }
  }, [db]);

  const doUpload = useCallback(async () => {
    setUploading(true);
    setStatusMsg(null);
    const result = await uploadFeed(db, (n, total) => {
      setStatusMsg({ ok: true, text: `Uploading ${n} / ${total}…` });
    });
    setUploading(false);
    if (result.error) {
      showMsg(false, result.error);
    } else if (!result.uploaded && !result.failed) {
      showMsg(true, 'Nothing to upload');
    } else {
      const parts: string[] = [];
      if (result.uploaded) parts.push(`${result.uploaded} uploaded`);
      if (result.failed)   parts.push(`${result.failed} failed`);
      showMsg(result.failed ? false : true, parts.join(', '));
    }
  }, [db]);

  function doRefresh() {
    setRefreshKey(k => k + 1);
  }

  function handleSearch(q: string) {
    setSearchQuery(q);
    setLimit(PAGE_SIZE);  // reset pagination when search changes
  }

  function loadMore() {
    if (hasMore) setLimit(l => l + PAGE_SIZE);
  }

  function toggleSelect(id: string) {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function cancelSelect() { setSelected(new Set()); }

  function confirmDeleteSelected() {
    const count = selected.size;
    Alert.alert(
      `Delete ${count} activit${count === 1 ? 'y' : 'ies'}`,
      'These activities will be permanently removed from your device.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: async () => {
            const ids = Array.from(selected);
            const paths = await deleteActivities(db, ids);
            setSelected(new Set());
            for (const p of paths) {
              if (p) try { await FileSystem.deleteAsync(p, { idempotent: true }); } catch {}
            }
          },
        },
      ],
    );
  }

  const busy = downloading || uploading;

  return (
    <View style={styles.container}>
      <View style={styles.headerRow}>
        {selecting ? (
          <>
            <Text style={styles.header}>{selected.size} selected</Text>
            <Pressable style={styles.cancelButton} onPress={cancelSelect}>
              <Text style={styles.cancelText}>Cancel</Text>
            </Pressable>
          </>
        ) : (
          <>
            <Text style={styles.header}>Feed</Text>
            <View style={styles.actionButtons}>
              <ActionButton
                icon="↑"
                label="Upload"
                loading={uploading}
                disabled={busy}
                accent={theme.accent}
                dim={theme.dim}
                onPress={doUpload}
              />
              <ActionButton
                icon="↓"
                label="Download"
                loading={downloading}
                disabled={busy}
                accent={theme.accent}
                dim={theme.dim}
                onPress={doDownload}
              />
              <ActionButton
                icon="↺"
                label="Refresh"
                loading={false}
                disabled={busy}
                accent={theme.accent}
                dim={theme.dim}
                onPress={doRefresh}
              />
            </View>
          </>
        )}
      </View>

      {statusMsg && (
        <Text style={statusMsg.ok ? styles.msgOk : styles.msgErr}>{statusMsg.text}</Text>
      )}

      {!selecting && (
        <View style={styles.searchRow}>
          <TextInput
            style={styles.searchInput}
            value={searchQuery}
            onChangeText={handleSearch}
            placeholder="Search activities…"
            placeholderTextColor="#52525b"
            returnKeyType="search"
            clearButtonMode="while-editing"
          />
        </View>
      )}

      {activities.length === 0 && !busy ? (
        <View style={styles.empty}>
          <Text style={styles.emptyIcon}>🚴</Text>
          <Text style={styles.emptyTitle}>No activities yet</Text>
          <Text style={styles.emptyBody}>
            Import a file or tap ↓ to pull from your instance.
          </Text>
        </View>
      ) : (
        <FlatList
          data={activities}
          keyExtractor={(a) => a.id}
          extraData={refreshKey}
          renderItem={({ item }) => (
            <ActivityCard
              activity={item}
              selecting={selecting}
              checked={selected.has(item.id)}
              onToggleSelect={() => toggleSelect(item.id)}
              onLongPress={() => toggleSelect(item.id)}
            />
          )}
          contentContainerStyle={styles.list}
          onEndReached={loadMore}
          onEndReachedThreshold={0.3}
          refreshControl={
            <RefreshControl
              refreshing={false}
              onRefresh={doRefresh}
              tintColor="#60a5fa"
            />
          }
        />
      )}

      {selecting && (
        <View style={styles.actionBar}>
          <Pressable style={styles.deleteBarButton} onPress={confirmDeleteSelected}>
            <Text style={styles.deleteBarText}>Delete {selected.size}</Text>
          </Pressable>
        </View>
      )}
    </View>
  );
}

function ActionButton({
  icon, label, loading, disabled, accent, dim, onPress,
}: {
  icon: string;
  label: string;
  loading: boolean;
  disabled: boolean;
  accent: string;
  dim: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      style={[styles.actionBtn, { backgroundColor: dim }, disabled && styles.actionBtnDisabled]}
      onPress={disabled ? undefined : onPress}
      accessibilityLabel={label}
    >
      <Text style={[styles.actionBtnIcon, { color: loading ? '#52525b' : accent }]}>
        {loading ? '…' : icon}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#09090b' },
  headerRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingTop: 60, paddingBottom: 12,
  },
  header: { color: '#fff', fontSize: 22, fontWeight: '700' },
  actionButtons: { flexDirection: 'row', gap: 8 },
  actionBtn: {
    width: 36, height: 36, borderRadius: 8,
    alignItems: 'center', justifyContent: 'center',
  },
  actionBtnDisabled: { opacity: 0.4 },
  actionBtnIcon: { fontSize: 18, fontWeight: '700', lineHeight: 22 },
  cancelButton: {
    backgroundColor: '#27272a', borderRadius: 8,
    paddingHorizontal: 14, paddingVertical: 7,
  },
  cancelText: { color: '#a1a1aa', fontSize: 13, fontWeight: '600' },
  msgOk: { color: '#86efac', fontSize: 12, textAlign: 'center', paddingHorizontal: 16, paddingBottom: 8 },
  msgErr: { color: '#fca5a5', fontSize: 12, textAlign: 'center', paddingHorizontal: 16, paddingBottom: 8 },
  searchRow: { paddingHorizontal: 16, paddingBottom: 10 },
  searchInput: {
    backgroundColor: '#18181b', borderWidth: 1, borderColor: '#27272a',
    borderRadius: 8, paddingHorizontal: 12, paddingVertical: 8,
    color: '#f4f4f5', fontSize: 14,
  },
  list:  { padding: 16, gap: 12, paddingBottom: 80 },
  empty: {
    flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32,
  },
  emptyIcon:  { fontSize: 48, marginBottom: 16 },
  emptyTitle: { color: '#f4f4f5', fontSize: 18, fontWeight: '600', marginBottom: 8 },
  emptyBody:  { color: '#71717a', fontSize: 14, textAlign: 'center', lineHeight: 20 },
  actionBar: {
    position: 'absolute', bottom: 0, left: 0, right: 0,
    backgroundColor: '#18181b', borderTopWidth: 1, borderTopColor: '#27272a',
    paddingHorizontal: 16, paddingVertical: 12, paddingBottom: 28,
  },
  deleteBarButton: {
    backgroundColor: '#7f1d1d', borderRadius: 10,
    paddingVertical: 14, alignItems: 'center',
  },
  deleteBarText: { color: '#fca5a5', fontSize: 15, fontWeight: '700' },
});
