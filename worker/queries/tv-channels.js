import { jsonResponse } from '../shared/cors.js';

// TV channel data — hardcoded from data/tv.csv
// (Cloudflare Workers can't read local files, so we embed the data)
const TV_CHANNELS = [
  { name: 'NDTV 24x7 Live', language: 'English', video_id: 'YeNJ2qeGM6Y' },
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
  { name: 'ABP Ananda Live', language: 'বাংলা', video_id: 'Q3MIjMycgVU' },
  { name: 'News18 Bangla', language: 'বাংলা', video_id: 'SbsFKzSh4vk' },
];

export function handleTvChannels() {
  return jsonResponse({ channels: TV_CHANNELS });
}
