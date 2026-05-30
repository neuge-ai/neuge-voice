import React from "react";

export function VoiceOrb({ isSpeaking, isListening, isUserSpeaking, userAudioLevel }: { isSpeaking: boolean; isListening: boolean; isUserSpeaking: boolean; userAudioLevel: number }) {
  let state = 'idle';
  if (isSpeaking) {
    state = 'ai-speaking';
  } else if (isUserSpeaking) {
    state = 'user-speaking';
  } else if (isListening) {
    state = 'listening';
  }

  return (
    <div className="relative w-80 h-80 transition-all duration-500 ease-in-out">
      {/* Outer Glow/Pulse Layer */}
      <div 
        className={`absolute inset-0 rounded-full opacity-80 blur-[2px] transition-all duration-500 ${
          state === 'idle' ? 'bg-gradient-to-br from-primary via-[#b39ddb] to-primaryContainer animate-orb-glow' :
          state === 'ai-speaking' ? 'bg-gradient-to-br from-primary via-[#b39ddb] to-primaryContainer animate-orb-glow-intense' :
          state === 'listening' ? 'bg-cyanCore/20 animate-sonar-pulse' :
          'bg-cyanCore/40 animate-erratic-vibe'
        }`} 
      />
      
      {/* Overlays */}
      <div className="absolute inset-0 bg-gradient-to-tr from-white/40 to-transparent rounded-full mix-blend-overlay"></div>
      <div className="absolute inset-2 bg-gradient-to-bl from-black/20 to-transparent rounded-full mix-blend-multiply"></div>
      
      {/* Inner glowing core */}
      <div 
        className={`absolute inset-0 m-auto w-3/4 h-3/4 rounded-full blur-xl opacity-60 transition-all duration-500 animate-orb-morph ${
          state === 'idle' ? 'bg-primary animate-pulse-slow' :
          state === 'ai-speaking' ? 'bg-primary animate-ai-speech-pulse' :
          state === 'listening' ? 'bg-cyanCore animate-pulse-slow' :
          'bg-cyanCore animate-erratic-vibe'
        }`} 
      />
    </div>
  );
}
