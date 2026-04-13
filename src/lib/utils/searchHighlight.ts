export type HighlightSegment = {
	text: string;
	match: boolean;
};

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const COMBINING_MARKS_RE = /[\u0300-\u036f]/g;

const normalizeForSearch = (value: string) => {
	const normalizedChars: string[] = [];
	const indexMap: number[] = [];

	for (let index = 0; index < value.length; index += 1) {
		const normalizedChunk = value[index].normalize('NFD').replace(COMBINING_MARKS_RE, '').toLowerCase();
		for (const character of normalizedChunk) {
			normalizedChars.push(character);
			indexMap.push(index);
		}
	}

	return {
		text: normalizedChars.join(''),
		indexMap
	};
};

export const splitHighlightedText = (
	value: string | null | undefined,
	term: string | null | undefined
): HighlightSegment[] => {
	const text = value ?? '';
	const needle = term?.trim() ?? '';

	if (!needle) {
		return [{ text, match: false }];
	}

	const normalizedText = normalizeForSearch(text);
	const normalizedNeedle = normalizeForSearch(needle).text;
	if (!normalizedNeedle) {
		return [{ text, match: false }];
	}

	const pattern = new RegExp(escapeRegExp(normalizedNeedle), 'gi');
	const segments: HighlightSegment[] = [];
	let cursor = 0;

	for (const match of normalizedText.text.matchAll(pattern)) {
		const normalizedStart = match.index ?? 0;
		const normalizedEnd = normalizedStart + match[0].length;
		const start = normalizedText.indexMap[normalizedStart];
		const end = normalizedText.indexMap[normalizedEnd - 1] + 1;

		if (start > cursor) {
			segments.push({ text: text.slice(cursor, start), match: false });
		}

		segments.push({ text: text.slice(start, end), match: true });
		cursor = end;
	}

	if (cursor < text.length) {
		segments.push({ text: text.slice(cursor), match: false });
	}

	return segments.length > 0 ? segments : [{ text, match: false }];
};
