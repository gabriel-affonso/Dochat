export type HighlightSegment = {
	text: string;
	match: boolean;
};

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export const splitHighlightedText = (
	value: string | null | undefined,
	term: string | null | undefined
): HighlightSegment[] => {
	const text = value ?? '';
	const needle = term?.trim() ?? '';

	if (!needle) {
		return [{ text, match: false }];
	}

	const pattern = new RegExp(escapeRegExp(needle), 'gi');
	const segments: HighlightSegment[] = [];
	let cursor = 0;

	for (const match of text.matchAll(pattern)) {
		const start = match.index ?? 0;
		const end = start + match[0].length;

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
