import { jsonResponse } from '../shared/cors.js';

// TV channel data — hardcoded from data/tv.csv
// (Cloudflare Workers can't read local files, so we embed the data)
const TV_CHANNELS = [
  { name: 'NDTV 24x7 Live', language: 'English', video_id: 'rGsZuR2Lofc' },
  { name: 'NDTV India', language: 'हिन्दी', video_id: 'erHLEEVGfac' },
  { name: 'BBC News', language: 'हिन्दी', video_id: 'p0AkRmh8NPY' },
  { name: 'Republic TV 24x7 Live', language: 'English', video_id: 'Gupg41GJlpo' },
  { name: 'Republic Bharat Live', language: 'हिन्दी', video_id: '_fkKwaQlfi4' },
  { name: 'CNN News18 Live', language: 'English', video_id: 'rfDx1HMvXbQ' },
  { name: 'India TV Live', language: 'हिन्दी', video_id: '26RLYAam9B8' },
  { name: 'Zee News Live', language: 'हिन्दी', video_id: 'pZ50doen-Gc' },
  { name: 'Sun News Live', language: 'தமிழ்', video_id: '9M02G5c6x6w' },
  { name: 'Thanthi TV', language: 'தமிழ்', video_id: '8kjza8MzPXE' },
  { name: 'Asianet Suvarna News', language: 'ಕನ್ನಡ', video_id: 'F1m0ciwMs8c' },
  { name: 'Asianet News Live', language: 'മലയാളം', video_id: 's0LLVQeMmtU' },
  { name: 'ABP Ananda Live', language: 'বাংলা', video_id: 'Ysa_6hPKr-A' },
  { name: 'News18 Bangla', language: 'বাংলা', video_id: 'SbsFKzSh4vk' },
  { name: 'TV9 Kannada News LIVE', language: 'ಕನ್ನಡ', video_id: 'jdJoOhqCipA' },
  { name: 'Manorama News Live', language: 'മലയാളം', video_id: 'tgBTspqA5nY' },
  { name: 'TV9 Telugu News LIVE', language: 'తెలుగు', video_id: 'II_m28Bm-iM' },
  { name: 'ABP Majha Live', language: 'मराठी', video_id: 'b5_zeRYuLYg' },
  { name: 'TV9 Marathi News LIVE', language: 'मराठी', video_id: 'hfwPLazLbd4' },
  { name: 'TV9 Gujarati LIVE', language: 'ગુજરાતી', video_id: 'I6xEE8TTX08' },
  { name: 'News18 Gujarati Live', language: 'ગુજરાતી', video_id: 'dicu4a-lpWc' },
  { name: 'Kanak News Live', language: 'ଓଡ଼ିଆ', video_id: 'tL5zlzxpYqE' },
  { name: 'OTV News Live', language: 'ଓଡ଼ିଆ', video_id: 'UsReAmsQvao' },
  { name: 'PTC News Live', language: 'ਪੰਜਾਬੀ', video_id: 'iGrH1tC8LdI' },
  { name: 'News18 Punjab Live', language: 'ਪੰਜਾਬੀ', video_id: 'KMWcefrAKLg' },
  { name: 'Zee Salaam Live', language: 'اردو', video_id: 'OFSYDwMn8gY' },
  { name: 'News18 Assam/Northeast Live', language: 'অসমীয়া', video_id: 'NoU6HSlW3TQ' },
];

export function handleTvChannels() {
  return jsonResponse({ channels: TV_CHANNELS });
}
