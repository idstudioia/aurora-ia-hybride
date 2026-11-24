import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './styles/tokens.css';
import './styles/base.css';
import { captureYoutubeReturn } from './lib/youtubeReturn';

captureYoutubeReturn();   // before the first render: Google's code leaves the address at once
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><App /></React.StrictMode>
);
