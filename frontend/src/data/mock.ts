import chapter1 from "../assets/chapter-1.png"
import chapter2 from "../assets/chapter-2.png"
import chapter3 from "../assets/chapter-3.png"
import chapter4 from "../assets/chapter-4.png"
import posterEpisode from "../assets/poster-episode.png"
import project1 from "../assets/project-1.png"
import project2 from "../assets/project-2.png"
import project3 from "../assets/project-3.png"
import project4 from "../assets/project-4.png"
import type { DashboardData } from "./types"

export const mockDashboard: DashboardData = {
  currentEpisode: {
    id: "ep-12",
    title: "AI Podcast Episode 12",
    sourceType: "upload",
    mediaUrl: "",
    posterUrl: posterEpisode,
    durationSec: 3462,
    resolution: "1080p",
    status: "completed",
    language: "en",
    createdAt: "2024-05-16T09:00:00Z",
  },
  summary: {
    episodeId: "ep-12",
    content:
      "In this episode, the hosts explore the latest advancements in AI, focusing on practical applications, ethical considerations, and the future of human-AI collaboration. They discuss real-world use cases, debunk common myths, and share predictions for the next 5 years.",
    readTimeMin: 6,
    keyPoints: 8,
    confidence: 92,
    generatedBy: "VIDA",
  },
  transcript: [
    { id: "seg-1", startSec: 0, endSec: 9, speaker: "Alex", text: "Welcome back to the AI Podcast! I'm Alex, and I'm joined by my co-host, Sam." },
    { id: "seg-2", startSec: 9, endSec: 20, speaker: "Sam", text: "Hey everyone! Today we're diving into the real-world applications of AI that are making the biggest impact in 2024." },
    { id: "seg-3", startSec: 20, endSec: 34, speaker: "Alex", text: "Absolutely. From healthcare to creative tools, AI is everywhere. Let's start with healthcare—what's exciting you most?" },
    { id: "seg-4", startSec: 34, endSec: 48, speaker: "Sam", text: "The breakthroughs in protein folding predictions. Tools like AlphaFold have accelerated drug discovery by years." },
    { id: "seg-5", startSec: 48, endSec: 62, speaker: "Alex", text: "And on the creative side, generative AI is empowering creators like never before. But there are ethical questions we can't ignore." },
    { id: "seg-6", startSec: 62, endSec: 75, speaker: "Sam", text: "Exactly. Bias, transparency, and responsible deployment are critical as these systems scale." },
  ],
  chapters: [
    { id: "ch-1", startSec: 0, title: "Introduction & Welcome", durationSec: 105, thumbnailUrl: chapter1, bookmarked: false },
    { id: "ch-2", startSec: 105, title: "AI in Healthcare", durationSec: 552, thumbnailUrl: chapter2, bookmarked: false },
    { id: "ch-3", startSec: 717, title: "Creative AI Revolution", durationSec: 548, thumbnailUrl: chapter3, bookmarked: false },
    { id: "ch-4", startSec: 1265, title: "Ethical Considerations", durationSec: 456, thumbnailUrl: chapter4, bookmarked: false },
    { id: "ch-5", startSec: 1721, title: "The Future of AI", durationSec: 382, thumbnailUrl: chapter1, bookmarked: false },
    { id: "ch-6", startSec: 2060, title: "Q&A and Closing Thoughts", durationSec: 1402, thumbnailUrl: chapter2, bookmarked: false },
  ],
  recentProjects: [
    { id: "ep-12", title: "AI Podcast Episode 12", createdAt: "2024-05-16T09:00:00Z", durationSec: 3462, status: "completed", thumbnailUrl: project1 },
    { id: "ep-11", title: "Product Launch Talk", createdAt: "2024-05-14T09:00:00Z", durationSec: 2058, status: "completed", thumbnailUrl: project2 },
    { id: "ep-10", title: "Customer Interview #7", createdAt: "2024-05-10T09:00:00Z", durationSec: 1351, status: "completed", thumbnailUrl: project3 },
    { id: "ep-9", title: "Design Sprint Debrief", createdAt: "2024-05-08T09:00:00Z", durationSec: 2469, status: "processing", thumbnailUrl: project4 },
  ],
}
