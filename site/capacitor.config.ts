import { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.bincio.activity',
  appName: 'BincioActivity',
  webDir: 'dist',
  server: {
    // Use https scheme on Android so cookies and service workers behave like a real origin
    androidScheme: 'https',
  },
  plugins: {
    Geolocation: {
      // iOS: keys are added in ios/App/App/Info.plist by `cap add ios`
      // Android: permissions added in AndroidManifest.xml by `cap add android`
    },
  },
};

export default config;
