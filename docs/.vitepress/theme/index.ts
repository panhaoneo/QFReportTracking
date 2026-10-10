import DefaultTheme from 'vitepress/theme'
import ReportTable from './components/ReportTable.vue'
import './style.css'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }: { app: any }) {
    app.component('ReportTable', ReportTable)
  },
}
