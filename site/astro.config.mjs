import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://zawa356.github.io',
  base: '/Utteran',
  integrations: [
    starlight({
      title: 'Utteran',
      description: 'Local audio and video transcription with speaker diarization.',
      head: [
        {
          tag: 'meta',
          attrs: {
            name: 'google-site-verification',
            content: '58893kAnr9_5C6gKmWdmq9taK_8na3rJao_DuXWhuAk',
          },
        },
      ],
      defaultLocale: 'root',
      locales: {
        root: { label: '日本語', lang: 'ja' },
        en: { label: 'English', lang: 'en' },
      },
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/zawa356/Utteran' },
      ],
      sidebar: [
        { slug: 'getting-started' },
        { slug: 'features' },
        { slug: 'hardware' },
        { slug: 'privacy' },
        { slug: 'faq' },
      ],
      editLink: {
        baseUrl: 'https://github.com/zawa356/Utteran/edit/main/site/',
      },
      credits: true,
    }),
  ],
});
