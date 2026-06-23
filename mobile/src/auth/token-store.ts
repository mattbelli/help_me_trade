import * as SecureStore from 'expo-secure-store';

const TOKEN_KEY = 'hmt_api_token';

/** Read the stored API token from the iOS Keychain (via expo-secure-store). */
export async function getToken(): Promise<string | null> {
  return SecureStore.getItemAsync(TOKEN_KEY);
}

/** Persist the API token to secure storage. */
export async function setToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_KEY, token);
}

/** Remove the stored API token. */
export async function clearToken(): Promise<void> {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
}
