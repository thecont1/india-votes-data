const PARTY_COLORS = {
  'BJP': '#FF6B00', 'INC': '#1E90FF', 'DMK': '#FF0000', 'AIADMK': '#008000',
  'AITC': '#00FF7F', 'CPM': '#CC0000', 'CPI(M)': '#CC0000', 'TVK': '#FFD700',
  'IUML': '#006400', 'AINRC': '#808080', 'CPI': '#8B0000', 'BPF': '#00CED1',
  'AGP': '#32CD32', 'VCK': '#8A2BE2', 'PMK': '#A9A9A9', 'IND': '#D3D3D3',
  'NCP': '#00008B', 'JD(U)': '#008080', 'SHS': '#FF4500', 'TDP': '#FFD700',
  'YSRCP': '#1E90FF', 'AAP': '#0066CC', 'BRS': '#FF69B4',
};
const DEFAULT_COLOR = '#888888';

export function getColor(abv, dbColor) {
  return PARTY_COLORS[abv] || dbColor || DEFAULT_COLOR;
}
