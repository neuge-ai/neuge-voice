import React from "react";

export function VoiceOrb({ isSpeaking, isListening, isUserSpeaking, userAudioLevel }: { isSpeaking: boolean; isListening: boolean; isUserSpeaking: boolean; userAudioLevel: number }) {
  let stateClass = 'state-idle';
  if (isSpeaking) {
    stateClass = 'state-speaking-ai';
  } else if (isUserSpeaking) {
    stateClass = 'state-speaking-user';
  } else if (isListening) {
    stateClass = 'state-listening';
  }

  return (
    <div className={`voice-orb ${stateClass} w-[40vmin] h-[40vmin] max-w-[280px] max-h-[280px] md:max-w-[480px] md:max-h-[480px] rounded-full relative flex items-center justify-center transition-transform duration-700 hover:scale-[1.03]`}>
      <div className="orb-core absolute w-[60%] h-[60%] rounded-full blur-xl mix-blend-screen"></div>
      <div className="absolute w-[80%] h-[80%] rounded-full bg-gradient-to-b from-transparent to-black/20 blur-2xl"></div>
      
      <div className="absolute inset-0 rounded-full border border-white/5"></div>
      
      <div className="inner-ripple-1 absolute inset-[-10%] rounded-full border opacity-0 mix-blend-screen"></div>
      <div className="inner-ripple-2 absolute inset-[-20%] rounded-full border opacity-0 mix-blend-screen"></div>
    </div>
  );
}
