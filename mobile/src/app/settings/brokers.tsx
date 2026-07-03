import { useState, type ReactNode } from 'react';
import { ScrollView, Switch, TextInput, View } from 'react-native';
import { useRouter } from 'expo-router';

import { useSettings, useUpdateSettings } from '@/api/queries';
import {
  BackRow,
  Button,
  Card,
  ErrorState,
  PressableScale,
  Row,
  Screen,
  Skeleton,
  Stack,
  Text,
} from '@/components/ui';
import { hairline, radius, spacing, useColors } from '@/theme';

const MASK = '********';

type PrimaryBroker = 'alpaca' | 'schwab';

interface AlpacaDraft {
  enabled: boolean;
  apiKeyId: string;
  apiSecretKey: string;
  apiBaseUrl: string;
  dataBaseUrl: string;
  dataFeed: string;
}

interface SchwabDraft {
  enabled: boolean;
  apiKey: string;
  appSecret: string;
  callbackUrl: string;
  accountHash: string;
}

interface SecretMeta {
  alpaca: { apiKeyId: boolean; apiSecretKey: boolean };
  schwab: { apiKey: boolean; appSecret: boolean };
}

interface BrokersDraft {
  primaryBroker: PrimaryBroker;
  alpaca: AlpacaDraft;
  schwab: SchwabDraft;
}

interface BrokersFormProps {
  initial: BrokersDraft;
  secretMeta: SecretMeta;
}

function isUserProvidedSecret(value: string): boolean {
  return value.length > 0 && value !== MASK;
}

function secretDraftValue(raw: unknown): string {
  if (typeof raw !== 'string') return '';
  return raw === MASK ? '' : raw;
}

function textDraftValue(raw: unknown, fallback = ''): string {
  return typeof raw === 'string' ? raw : fallback;
}

function wasSavedSecret(raw: unknown): boolean {
  return raw === MASK;
}

function parseBrokers(data: Record<string, unknown> | undefined): BrokersFormProps {
  const primary =
    data?.primary_broker === 'schwab' ? 'schwab' : ('alpaca' as PrimaryBroker);

  const brokersRaw = data?.brokers;
  const brokers =
    brokersRaw && typeof brokersRaw === 'object'
      ? (brokersRaw as Record<string, unknown>)
      : {};

  const alpacaRaw = brokers.alpaca;
  const alpacaObj =
    alpacaRaw && typeof alpacaRaw === 'object'
      ? (alpacaRaw as Record<string, unknown>)
      : {};

  const schwabRaw = brokers.schwab;
  const schwabObj =
    schwabRaw && typeof schwabRaw === 'object'
      ? (schwabRaw as Record<string, unknown>)
      : {};

  return {
    initial: {
      primaryBroker: primary,
      alpaca: {
        enabled: Boolean(alpacaObj.enabled ?? true),
        apiKeyId: secretDraftValue(alpacaObj.api_key_id),
        apiSecretKey: secretDraftValue(alpacaObj.api_secret_key),
        apiBaseUrl: textDraftValue(
          alpacaObj.api_base_url,
          'https://paper-api.alpaca.markets',
        ),
        dataBaseUrl: textDraftValue(
          alpacaObj.data_base_url,
          'https://data.alpaca.markets',
        ),
        dataFeed: textDraftValue(alpacaObj.data_feed, 'iex'),
      },
      schwab: {
        enabled: Boolean(schwabObj.enabled ?? false),
        apiKey: secretDraftValue(schwabObj.api_key),
        appSecret: secretDraftValue(schwabObj.app_secret),
        callbackUrl: textDraftValue(schwabObj.callback_url, 'https://127.0.0.1:8182'),
        accountHash: textDraftValue(schwabObj.account_hash),
      },
    },
    secretMeta: {
      alpaca: {
        apiKeyId: wasSavedSecret(alpacaObj.api_key_id),
        apiSecretKey: wasSavedSecret(alpacaObj.api_secret_key),
      },
      schwab: {
        apiKey: wasSavedSecret(schwabObj.api_key),
        appSecret: wasSavedSecret(schwabObj.app_secret),
      },
    },
  };
}

function buildBrokersPatch(draft: BrokersDraft): Record<string, unknown> {
  const alpaca: Record<string, unknown> = {
    enabled: draft.alpaca.enabled,
    api_base_url: draft.alpaca.apiBaseUrl.trim(),
    data_base_url: draft.alpaca.dataBaseUrl.trim(),
    data_feed: draft.alpaca.dataFeed.trim(),
  };
  if (isUserProvidedSecret(draft.alpaca.apiKeyId)) {
    alpaca.api_key_id = draft.alpaca.apiKeyId.trim();
  }
  if (isUserProvidedSecret(draft.alpaca.apiSecretKey)) {
    alpaca.api_secret_key = draft.alpaca.apiSecretKey.trim();
  }

  const schwab: Record<string, unknown> = {
    enabled: draft.schwab.enabled,
    callback_url: draft.schwab.callbackUrl.trim(),
    account_hash: draft.schwab.accountHash.trim(),
  };
  if (isUserProvidedSecret(draft.schwab.apiKey)) {
    schwab.api_key = draft.schwab.apiKey.trim();
  }
  if (isUserProvidedSecret(draft.schwab.appSecret)) {
    schwab.app_secret = draft.schwab.appSecret.trim();
  }

  return {
    primary_broker: draft.primaryBroker,
    brokers: { alpaca, schwab },
  };
}

export default function BrokersSettingsScreen() {
  const router = useRouter();
  const settings = useSettings();

  if (settings.isError) {
    return (
      <Screen>
        <View style={{ flex: 1, paddingTop: spacing.sm, paddingHorizontal: spacing.xl }}>
          <BackRow onBack={() => router.back()} />
          <ErrorState onRetry={settings.refetch} />
        </View>
      </Screen>
    );
  }

  return (
    <Screen>
      <ScrollView
        contentContainerStyle={{
          paddingTop: spacing.sm,
          paddingHorizontal: spacing.xl,
          paddingBottom: 110,
        }}
      >
        <Stack gap={spacing.lg}>
          <BackRow onBack={() => router.back()} />
          <Text variant="title">Brokers</Text>

          {settings.isLoading || !settings.data ? (
            <Stack gap={spacing.md}>
              <Skeleton height={52} />
              <Skeleton height={120} />
              <Skeleton height={120} />
            </Stack>
          ) : (
            <BrokersForm
              key={settings.dataUpdatedAt}
              {...parseBrokers(settings.data as Record<string, unknown>)}
            />
          )}
        </Stack>
      </ScrollView>
    </Screen>
  );
}

function BrokersForm({ initial, secretMeta }: BrokersFormProps) {
  const colors = useColors();
  const update = useUpdateSettings();
  const [draft, setDraft] = useState(initial);
  const [justSaved, setJustSaved] = useState(false);

  const handleSave = () => {
    update.mutate(buildBrokersPatch(draft), {
      onSuccess: () => {
        setJustSaved(true);
        setTimeout(() => setJustSaved(false), 1500);
      },
    });
  };

  const setAlpaca = (patch: Partial<AlpacaDraft>) => {
    setDraft((d) => ({ ...d, alpaca: { ...d.alpaca, ...patch } }));
  };

  const setSchwab = (patch: Partial<SchwabDraft>) => {
    setDraft((d) => ({ ...d, schwab: { ...d.schwab, ...patch } }));
  };

  return (
    <Stack gap={spacing.lg}>
      <Stack gap={spacing.sm}>
        <Text variant="label" upper color="textFaint">
          Primary broker
        </Text>
        <Row gap={spacing.xs}>
          {(['alpaca', 'schwab'] as const).map((broker) => {
            const active = draft.primaryBroker === broker;
            const label = broker === 'alpaca' ? 'Alpaca' : 'Schwab';
            return (
              <PressableScale
                key={broker}
                onPress={() => setDraft((d) => ({ ...d, primaryBroker: broker }))}
              >
                <View
                  style={{
                    paddingHorizontal: spacing.lg,
                    paddingVertical: spacing.sm,
                    borderRadius: 999,
                    backgroundColor: active ? colors.accent : colors.surfaceRaised,
                    borderWidth: hairline,
                    borderColor: active ? colors.accent : colors.border,
                  }}
                >
                  <Text
                    variant="caption"
                    style={{ color: active ? colors.onAccent : colors.textMuted }}
                  >
                    {label}
                  </Text>
                </View>
              </PressableScale>
            );
          })}
        </Row>
      </Stack>

      <Card>
        <Stack gap={spacing.md}>
          <Row justify="space-between" align="center">
            <Text variant="bodyStrong">Alpaca</Text>
            <Switch
              value={draft.alpaca.enabled}
              onValueChange={(enabled) => setAlpaca({ enabled })}
              trackColor={{ false: colors.border, true: colors.accent }}
              thumbColor={colors.surfaceRaised}
            />
          </Row>

          <LabeledField label="API Key ID">
            <SettingsTextInput
              value={draft.alpaca.apiKeyId}
              onChangeText={(apiKeyId) => setAlpaca({ apiKeyId })}
              placeholder={secretMeta.alpaca.apiKeyId ? 'Saved — type to replace' : 'PK…'}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
            />
          </LabeledField>

          <LabeledField label="API Secret Key">
            <SettingsTextInput
              value={draft.alpaca.apiSecretKey}
              onChangeText={(apiSecretKey) => setAlpaca({ apiSecretKey })}
              placeholder={
                secretMeta.alpaca.apiSecretKey ? 'Saved — type to replace' : 'Secret key'
              }
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
            />
          </LabeledField>

          <LabeledField label="API Base URL">
            <SettingsTextInput
              value={draft.alpaca.apiBaseUrl}
              onChangeText={(apiBaseUrl) => setAlpaca({ apiBaseUrl })}
              placeholder="https://paper-api.alpaca.markets"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
            />
          </LabeledField>

          <LabeledField label="Data Base URL">
            <SettingsTextInput
              value={draft.alpaca.dataBaseUrl}
              onChangeText={(dataBaseUrl) => setAlpaca({ dataBaseUrl })}
              placeholder="https://data.alpaca.markets"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
            />
          </LabeledField>

          <LabeledField label="Data Feed">
            <SettingsTextInput
              value={draft.alpaca.dataFeed}
              onChangeText={(dataFeed) => setAlpaca({ dataFeed })}
              placeholder="iex"
              autoCapitalize="none"
              autoCorrect={false}
            />
          </LabeledField>
        </Stack>
      </Card>

      <Card>
        <Stack gap={spacing.md}>
          <Row justify="space-between" align="center">
            <Text variant="bodyStrong">Schwab</Text>
            <Switch
              value={draft.schwab.enabled}
              onValueChange={(enabled) => setSchwab({ enabled })}
              trackColor={{ false: colors.border, true: colors.accent }}
              thumbColor={colors.surfaceRaised}
            />
          </Row>

          <LabeledField label="App Key">
            <SettingsTextInput
              value={draft.schwab.apiKey}
              onChangeText={(apiKey) => setSchwab({ apiKey })}
              placeholder={secretMeta.schwab.apiKey ? 'Saved — type to replace' : 'App key'}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
            />
          </LabeledField>

          <LabeledField label="App Secret">
            <SettingsTextInput
              value={draft.schwab.appSecret}
              onChangeText={(appSecret) => setSchwab({ appSecret })}
              placeholder={
                secretMeta.schwab.appSecret ? 'Saved — type to replace' : 'App secret'
              }
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
            />
          </LabeledField>

          <LabeledField label="Callback URL">
            <SettingsTextInput
              value={draft.schwab.callbackUrl}
              onChangeText={(callbackUrl) => setSchwab({ callbackUrl })}
              placeholder="https://127.0.0.1:8182"
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="url"
            />
          </LabeledField>

          <LabeledField label="Account Hash">
            <SettingsTextInput
              value={draft.schwab.accountHash}
              onChangeText={(accountHash) => setSchwab({ accountHash })}
              placeholder=""
              autoCapitalize="none"
              autoCorrect={false}
            />
          </LabeledField>

          <Text variant="caption" color="textMuted">
            OAuth re-auth runs on the server (scripts.schwab_login).
          </Text>
        </Stack>
      </Card>

      <Button
        label={justSaved ? 'Saved' : 'Save'}
        onPress={handleSave}
        disabled={update.isPending || justSaved}
      />
    </Stack>
  );
}

function LabeledField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Stack gap={spacing.sm}>
      <Text variant="label" upper color="textFaint">
        {label}
      </Text>
      {children}
    </Stack>
  );
}

function SettingsTextInput({
  value,
  onChangeText,
  placeholder,
  secureTextEntry,
  keyboardType,
  autoCapitalize,
  autoCorrect,
}: {
  value: string;
  onChangeText: (text: string) => void;
  placeholder?: string;
  secureTextEntry?: boolean;
  keyboardType?: 'default' | 'url';
  autoCapitalize?: 'none' | 'sentences' | 'words' | 'characters';
  autoCorrect?: boolean;
}) {
  const colors = useColors();

  return (
    <TextInput
      value={value}
      onChangeText={onChangeText}
      placeholder={placeholder}
      placeholderTextColor={colors.textFaint}
      secureTextEntry={secureTextEntry}
      keyboardType={keyboardType}
      autoCapitalize={autoCapitalize}
      autoCorrect={autoCorrect}
      selectionColor={colors.accent}
      style={{
        color: colors.text,
        fontSize: 16,
        paddingHorizontal: spacing.lg,
        paddingVertical: spacing.md,
        backgroundColor: colors.bgInset,
        borderRadius: radius.lg,
        borderWidth: hairline,
        borderColor: colors.border,
      }}
    />
  );
}
