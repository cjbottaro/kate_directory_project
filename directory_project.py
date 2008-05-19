__title__ = "Directory Project"
__author__ = "Christopher J. Bottaro <cjbottaro@alumni.cs.utexas.edu>"
__license__ = "LGPL"
__name__ = "directory_project"

from qt import * #QObject, QDir, SIGNAL, QFileInfo, QVBoxLayout, QString, QSize, Qt, QGroupBox, QHBoxLayout
from kdeui import * #KListView, KListViewItem, KListViewSearchLine, KListViewSearchLineWidget, KLineEdit, KDialog
from kfile import KFileDialog
from kio import KMimeType, KDirWatch
from kdecore import KURL, KIcon, KIconLoader, KShortcut
import kate
import kate.gui
from ConfigParser import ConfigParser
from time import time

dProject = None

class OpenStruct:
  pass

class PixmapSetter:
  
  dir_pixmap = None

  @staticmethod
  def set(lvi, path, is_dir = False):
    if is_dir:
      if not PixmapSetter.dir_pixmap:
        kate.debug("caching directory pixmap")
        PixmapSetter.dir_pixmap = KMimeType.findByPath(path).pixmap(KIcon.Small)
      pixmap = PixmapSetter.dir_pixmap
    else:
      pixmap = KMimeType.findByPath(path, 0, True).pixmap(KIcon.Small)
    lvi.setPixmap(0, pixmap)


class ListViewItem(KListViewItem):

  def __init__(self, parent, label, path):
    KListViewItem.__init__(self, parent, label)
    self.path = path
    self.is_dir = False
    

class DirectoryProject():

  def __init__(self, tool_widget):

    # we have to init the config first because all the widget __init__ methods below need it too
    self.initConfig()

    # all of our "child" widget
    self.browser  = DPBrowser(tool_widget)
    self.finder = DPFinder(self)
    self.settings = DPSettings(self)
    
    self.dir_watcher = None
    self.open_project = None

    QObject.connect(self.browser, SIGNAL("doubleClicked ( QListViewItem *, const QPoint &, int )"), self.openItem)
    QObject.connect(self.finder.list_view, SIGNAL("doubleClicked ( QListViewItem *, const QPoint &, int )"), self.openItem)

    self.initMenu()

    last_project_path = self.config.get('general', 'last')
    if last_project_path:
      self.openProject(last_project_path)


  def initMenu(self):
    # ugh, we gotta get a KActionCollection instance somehow
    action_collection = kate.sip.cast(kate.mainWidget().topLevelWidget(), KMainWindow).actionCollection()
    
    # because our KPopupMenu has no parent, we have to keep it from going out of scope
    self.menu = KPopupMenu()

    # Open
    action = KAction("&Open...", "fileopen", KShortcut('Ctrl+Shift+O'), self.menuOpen, action_collection)
    action.plug(self.menu)

    # Close
    action = KAction("&Close", "fileclose", KShortcut.null(), self.menuClose, action_collection)
    action.plug(self.menu)

    # Reload
    action = KAction("&Reload", "reload", KShortcut('Shift+F5'), self.menuReload, action_collection)
    action.plug(self.menu)

    self.menu.insertSeparator()

    # Find Files
    action = KAction("&Find Files...", "find", KShortcut('Ctrl+H'), self.menuFindFiles, action_collection)
    action.plug(self.menu)

    self.menu.insertSeparator()

    # Settings
    action = KAction("&Settings...", "configure", KShortcut.null(), self.menuSettings, action_collection)
    action.plug(self.menu)
    
    # insert the menu into the menu bar
    menu_bar = kate.mainWidget().topLevelWidget().menuBar()
    menu_bar.insertItem("&Project", self.menu, -1, 4)
    
    
  def initConfig(self):
    kate.debug("initConfig()")
    config_path = kate.pate.pluginDirectories[1] + "/%s/%s.conf" % (__name__, __name__)
    config_file = QFileInfo(config_path)
    
    if not config_file.exists():
      open(config_path, "w").close()

    config = ConfigParser()
    config.read(config_path)

    # init the DEFAULT options if they don't exist
    # the DEFAULT section is special and doesn't need to be created: if not config.has_section('DEFAULT'): config.add_section('DEFAULT')
    if not config.has_option('DEFAULT', 'ignore'): config.set('DEFAULT', 'ignore', '')
    if not config.has_option('DEFAULT', 'filter'): config.set('DEFAULT', 'filter', '*')
    if not config.has_option('DEFAULT', 'finder_size'): config.set('DEFAULT', 'finder_size', '400x450')
    if not config.has_option('DEFAULT', 'config_size'): config.set('DEFAULT', 'config_size', '300x350')
    if not config.has_option('DEFAULT', 'search_type'): config.set('DEFAULT', 'search_type', 'word')
      
    # create the general section if it doesn't exist
    if not config.has_section('general'): config.add_section('general')

    # flush the config file
    config.write(open(config_path, "w"))

    # save the config object and config path as instance vars for use later
    self.config = config
    self.config_path = config_path

  # end def initConfig()

  # convenience method to get a config option for the currently open project
  def get_option(self, option_name):
    return self.config.get(self.open_project, option_name)


  # convenience method that sets a config option for the currently open project
  def set_option(self, name, value):
    self.config.set(self.open_project, name, value)

  # convenience method to save the config file
  def saveConfig(self):
    self.config.write(open(self.config_path, "w"))

  def reload(self):

    # sanity checks
    if not self.sanityChecks(self.open_project): return

    # clear the widgets
    self.browser.clear()
    self.finder.clear()

    # parse the ignore list (used further down the call stack)
    self.ignore_list = self.get_option('ignore')
    if self.ignore_list:
      self.ignore_list = [x.strip() for x in self.ignore_list.split(',')]
    else:
      self.ignore_list = []

    # init the dir watcher
    self.dir_watcher = KDirWatch()
    QObject.connect(self.dir_watcher, SIGNAL("dirty ( const QString & )"), self.dirDirtied)
    QObject.connect(self.dir_watcher, SIGNAL("deleted ( const QString & )"), self.dirRemoved)
    
    # time and output the building of the tree
    t1 = time()
    self.addItem(QFileInfo(self.open_project), self.browser)
    kate.debug("project (re)load took %f seconds" % (time()-t1))
    
  # end def reload()

  def openProject(self, project_path):

    # clean up the input
    project_path = str(project_path).strip()

    # sanity checks
    if not self.sanityChecks(project_path): return

    # little debug info
    kate.debug("opening new project: " + project_path)

    # set the open project
    self.open_project = project_path

    # init its config section if need be
    if not self.config.has_section(project_path):
      self.config.add_section(project_path)

    # update the last open project in the config section
    self.config.set('general', 'last', project_path)

    # write the config so we always stay in sync
    self.saveConfig()

    # build the project tree!
    self.reload()

  # end def openProject()


  def passIgnore(self, file_info):
    file_name = file_info.fileName()
    if file_name == '.': return False
    if file_name == '..': return False
    if file_name in self.ignore_list: return False
    return True
  # end def passIgnore()


  def sanityChecks(self, project_path):
    file_info = QFileInfo(project_path)
    if not file_info.exists():
      kate.debug("project dir does not exist: %s" % project_path)
      return False
    if not file_info.isDir():
      kate.debug("project dir is not a directory: %s" % project_path)
      return False
    return True
  # end def sanityChecks()


  def openItem(self, item, trash1, trash2):
    self.openItems((item,))


  def openItems(self, items):
    for item in items:
      if not item: continue # what the hell?  something is broken
      if item.is_dir == False:
        kate.debug("file dbl clicked")
        kate.documentManager.open(item.path)
        d = kate.documentManager.get(item.path)
        kate.application.activeMainWindow().viewManager().activateView(d.number)
        self.finder.close()
      else:
        kate.debug("dir dbl clicked")
        self.browser.setOpen(item, not self.browser.isOpen(item))


  # we only care about files and added dirs here.
  def dirDirtied(self, path):
    path = str(path)
    kate.debug('dirDirtied: ' + path)

    # find the list view item for the watched directory
    lvi = self.browser.findItem(path, 1)
    if not lvi:
      kate.debug("cannot find directory: " + path)
      return

    # create a set of all it's children
    our_set = set()
    p = lvi.firstChild()
    while p:
      our_set.add(str(p.text(1)))
      p = p.nextSibling()

    # create a set of all the actual directory's children
    real_set = set()
    d = QDir(path)
    for file_info in d.entryInfoList():
      if file_info.fileName() == '.':
        continue
      if file_info.fileName() == '..':
        continue
      real_set.add(str(file_info.absFilePath()))

    # difference the set and those are our adds
    set_diff = real_set.difference(our_set)
    for path in set_diff:
      file_info = QFileInfo(path)
      self.addItem(file_info, lvi)
    kate.debug('added files/dirs: ' + str(set_diff))

    # differece the other way and those are our deletes
    set_diff = our_set.difference(real_set)
    kate.debug('removing files/dirs: ' + str(set_diff))
    for path in set_diff:
      self.removeItem(path)


  # we only care about removed dirs here.
  def dirRemoved(self, path):
    kate.debug('dirRemoved: ' + str(path))


  # unlike browser and finder's addItem(), this is recursive
  def addItem(self, file_info, parent):

    # base case (file)
    if file_info.isFile():
      if self.passIgnore(file_info):
        p = self.browser.addItem(file_info, parent)
        self.finder.addItem(file_info, p.pixmap(0))

    # recursive case (dir)
    else:
      d = QDir(file_info.absFilePath())

      if not self.passIgnore(file_info):
        return

      parent = self.browser.addItem(file_info, parent)
      
      # watch this dir for changes
      self.dir_watcher.addDir(file_info.absFilePath())
      
      # do directories first
      d.setFilter(QDir.Dirs)
      for file_info in d.entryInfoList():
        self.addItem(file_info, parent)

      # now do files
      d.setFilter(QDir.Files)
      d.setNameFilter(self.config.get(self.open_project, 'filter'))
      for file_info in d.entryInfoList():
        self.addItem(file_info, parent)
      
    
  # unlike browser and finder's removeItem(), this is recursive
  def removeItem(self, path):
    p = self.browser.findItem(path, 1)

    # base case (file)
    if p.childCount() == 0:
      self.browser.removeItem(path)
      self.finder.removeItem(path)

    # recursive case (dir)
    else:
      n = p.firstChild()
      while n:
        temp = n # the ole linked list gotcha:  if we delete n, then n.nextSibling() will return None
        n = n.nextSibling()
        self.removeItem(temp.text(1))
      self.browser.removeItem(path)
      self.dir_watcher.removeDir(path)


  def menuOpen(self):
    kate.debug('menuOpen()')
    project_path = KFileDialog.getExistingDirectory()
    if project_path: self.openProject(project_path)


  def menuClose(self):
    kate.debug('menuClose()')
    self.browser.clear()
    self.finder.clear()
    self.open_project = None
    self.config.set('general', 'last', '')
    self.saveConfig()
    

  def menuFindFiles(self):
    kate.debug('menuFindFiles()')
    if not self.open_project:
      KMessageBox.information(kate.mainWidget(), "Open a project first.", "No Project Open")
    else:
      self.finder.show()

  def menuReload(self):
    kate.debug('menuReload()')
    if self.open_project:
      self.reload()
    else:
      KMessageBox.information(kate.mainWidget(), "There is no project open to reload.", "No Project Open")

  def menuSettings(self):
    kate.debug('menuSettings()')
    self.settings.show()

# end class DirectoryProjectBrowser


class DPBrowser(KListView):

  def __init__(self, parent):
    KListView.__init__(self,  parent)
    self.header().hide()
    self.addColumn('')
    self.setRootIsDecorated(True)

  def addItem(self, file_info, parent):
    lvi = ListViewItem(parent, file_info.fileName(), file_info.absFilePath())
    lvi.is_dir = file_info.isDir()
    PixmapSetter.set(lvi, file_info.absFilePath(), file_info.isDir())
    return lvi

  def removeItem(self, path):
    lvi = self.findItem(path, 1)
    lvi.parent().takeItem(lvi)
    

class DPFinder(KDialog):
  
  def __init__(self,parent = None):
    KDialog.__init__(self, kate.mainWidget())

    self.dp = parent
    
    FindFilesDlgLayout = QVBoxLayout(self,11,6,"FindFilesDlgLayout")

    self.list_view = KListView(self)
    self.list_view.addColumn(QString.null)
    self.list_view.header().hide()

    self.lv_search = ListViewSearchLineWidget(self.list_view, self)
    
    FindFilesDlgLayout.addWidget(self.lv_search)
    FindFilesDlgLayout.addWidget(self.list_view)

    self.setSizeGripEnabled(True)

  # end def __init__

  def addItem(self, file_info, pixmap = None):
    project_path = self.dp.open_project
    name = file_info.fileName()
    full_path = file_info.absFilePath()
    dir_path = QString(full_path).remove(name)
    lvi = ListViewItem(self.list_view, name, full_path)
    if pixmap:
      lvi.setPixmap(0, pixmap)
    else:
      PixmapSetter.set(lvi, file_info.absFilePath())
  # end def addItem()

  def removeItem(self, path):
    lvi = self.list_view.findItem(path, 1)
    self.list_view.takeItem(lvi)
    
  def clear(self):
    self.list_view.clear()
  # end def clear()


  # overloaded virtual
  def show(self):

    # make sure the search line always has focus
    self.lv_search.searchLine().setFocus()

    # reset the list view
    self.lv_search.searchLine().resetListView()

    # use the user's preferred size
    x, y = self.dp.config.get('general', 'finder_size').split('x')
    x, y = int(x), int(y)
    size = QSize(x,y).expandedTo(self.minimumSizeHint())
    self.resize(size.width(), size.height())

    # finally show the thing
    KDialog.show(self)
    
  # end def show()


  # overloaded virtual
  def closeEvent(self, e):
    x, y = self.width(), self.height()
    x, y = int(x), int(y)

    old_x, old_y = self.dp.config.get('general', 'finder_size').split('x')
    old_x, old_y = int(old_x), int(old_y)

    if x != old_x or y != old_y:
      kate.debug('saving new finder dialog size: %dx%d' % (x, y))
      self.dp.config.set('general', 'finder_size', '%dx%d' % (x, y))
      self.dp.saveConfig()

    # default implementation...
    e.accept()


  # Holy crap, lucky us that the KListViewSearchLine doesn't know what to do with Key_Up, Key_Down, Key_Enter, Key_Return
  # events and passes them to its parent (i.e. us).
  def keyPressEvent(self, event):
    kate.debug("event: " + str(event.key()))
    if event.key() == Qt.Key_Up:
      if self.list_view.selectedItem():
        self.list_view.keyPressEvent(event)
      else:
        self.selectLastItem()
    elif event.key() == Qt.Key_Down:
      if self.list_view.selectedItem():
        self.list_view.keyPressEvent(event)
      else:
        self.selectFirstItem()
    elif event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
      self.dp.openItems((self.list_view.selectedItem(),))
    elif event.key() == Qt.Key_Escape:
      if not str(self.lv_search.searchLine().text()):
        self.close()
      else:
        self.lv_search.searchLine().clear()
    else:
      event.ignore()
  # end def keyPressEvent()


  def selectLastItem(self):
    item = self.list_view.lastChild()
    while item and not item.isVisible():
      item = item.previousSibling()
    if item:
      self.list_view.setSelected(item, True)
      self.list_view.ensureItemVisible(item)


  def selectFirstItem(self):
    item = self.list_view.firstChild()
    while item and not item.isVisible():
      item = item.nextSibling()
    if item:
      self.list_view.setSelected(item, True)

      
# end class FindFilesDlg

class ListViewSearchLine(KListViewSearchLine):

  def __init__(self, parent, list_view, name = ''):
    KListViewSearchLine.__init__(self, parent, list_view, name)

  def updateSearch(self, s = None):
    t1 = time()

    search_type = self.listView().parent().dp.config.get('general', 'search_type').lower()
    if search_type == 'exact':
      self.updateSearch_exact(s)
    elif search_type == 'char':
      self.updateSearch_char(s)
    elif search_type == 'word':
      self.updateSearch_word(s)
    else:
      kate.debug("unexpected search type: %s" % search_type)
      return

    kate.debug('updateSearch(%s): %f seconds for %d items' % (s, time()-t1, self.listView().childCount()))
    
    self.resetListView()

  # default KListViewSearchLine search:
  # ment_te =>
  #   docu[ment_te]st
  # 0.005 seconds
  def updateSearch_exact(self, s):
    KListViewSearchLine.updateSearch(self, s)

  # this search implements a TextMate like search:
  # doc test =>
  #   [d][o][c]ument_[t][e][s][t]
  #   [d]ashb[o]ar[d]_contoller_[t][e][s][t]
  # 0.04 seconds
  def updateSearch_char(self, s):
    lv = self.listView()

    # prime the pump
    hits = []
    item = lv.firstChild()
    while (item):
      hits.append( (-1, item) )
      item.setVisible(True)
      item = item.nextSibling()

    # start pumpin...
    for c in s:
      if len(hits) == 0: break
      if c == ' ': continue
      next_hits = []
      for hit in hits:
        i = hit[1].text(0).find(c, hit[0]+1)
        if i > -1:
          next_hits.append( (i, hit[1]) )
        else:
          hit[1].setVisible(False)
      hits = next_hits


  # this is my preferred search type:
  # doc test =>
  #   [doc]ument [test]
  #   [doc]ument controller [test]
  # 0.03 seconds
  def updateSearch_word(self, s):
    lv = self.listView()

    # prime the pump
    hits = []
    item = lv.firstChild()
    while (item):
      hits.append( (-1, item) )
      item.setVisible(True)
      item = item.nextSibling()

    # start pumpin...
    for w in str(s).split(' '):
      if len(hits) == 0: break
      if w == ' ': continue
      next_hits = []
      for hit in hits:
        i = hit[1].text(0).find(w, hit[0]+1)
        if i > -1:
          next_hits.append( (i, hit[1]) )
        else:
          hit[1].setVisible(False)
      hits = next_hits


  def resetListView(self):
    list_view = self.listView()
    list_view.clearSelection()
    first_item = list_view.firstChild()
    while first_item and not first_item.isVisible():
      first_item = first_item.nextSibling()
    if first_item:
      list_view.ensureItemVisible(first_item)
    

class ListViewSearchLineWidget(KListViewSearchLineWidget):

  def __init__(self, list_view, parent, name = None):
    KListViewSearchLineWidget.__init__(self, list_view, parent, name)
    self._searchLine = None # not be confused with the method searchLine()

  def createSearchLine(self, list_view):
    if not self._searchLine:
      self._searchLine = ListViewSearchLine(self, list_view)
    return self._searchLine



# generated by Designer and pyuic
class DPSettingsBase(QDialog):
    def __init__(self,parent = None,name = None,modal = 0,fl = 0):
        QDialog.__init__(self,parent,name,modal,fl)

        if not name:
            self.setName("DPSettings")

        self.setSizeGripEnabled(1)
        self.setModal(1)

        DPSettingsLayout = QVBoxLayout(self,11,6,"DPSettingsLayout")

        self.groupBox3_2 = QGroupBox(self,"groupBox3_2")
        self.groupBox3_2.setColumnLayout(0,Qt.Vertical)
        self.groupBox3_2.layout().setSpacing(6)
        self.groupBox3_2.layout().setMargin(11)
        groupBox3_2Layout = QHBoxLayout(self.groupBox3_2.layout())
        groupBox3_2Layout.setAlignment(Qt.AlignTop)

        self.textLabel1 = QLabel(self.groupBox3_2,"textLabel1")
        groupBox3_2Layout.addWidget(self.textLabel1)

        self.w_search_type = QComboBox(0,self.groupBox3_2,"w_search_type")
        groupBox3_2Layout.addWidget(self.w_search_type)
        spacer2 = QSpacerItem(40,20,QSizePolicy.Expanding,QSizePolicy.Minimum)
        groupBox3_2Layout.addItem(spacer2)
        DPSettingsLayout.addWidget(self.groupBox3_2)

        self.groupBox2 = QGroupBox(self,"groupBox2")
        self.groupBox2.setColumnLayout(0,Qt.Vertical)
        self.groupBox2.layout().setSpacing(6)
        self.groupBox2.layout().setMargin(11)
        groupBox2Layout = QVBoxLayout(self.groupBox2.layout())
        groupBox2Layout.setAlignment(Qt.AlignTop)

        layout3 = QHBoxLayout(None,0,6,"layout3")

        self.textLabel2 = QLabel(self.groupBox2,"textLabel2")
        layout3.addWidget(self.textLabel2)

        self.w_filters = KLineEdit(self.groupBox2,"w_filters")
        layout3.addWidget(self.w_filters)
        groupBox2Layout.addLayout(layout3)

        self.w_ignore = KEditListBox(self.groupBox2,"w_ignore")
        groupBox2Layout.addWidget(self.w_ignore)
        DPSettingsLayout.addWidget(self.groupBox2)

        Layout1 = QHBoxLayout(None,0,6,"Layout1")
        Horizontal_Spacing2 = QSpacerItem(20,20,QSizePolicy.Expanding,QSizePolicy.Minimum)
        Layout1.addItem(Horizontal_Spacing2)

        self.buttonOk = QPushButton(self,"buttonOk")
        self.buttonOk.setAutoDefault(1)
        self.buttonOk.setDefault(1)
        Layout1.addWidget(self.buttonOk)

        self.buttonCancel = QPushButton(self,"buttonCancel")
        self.buttonCancel.setAutoDefault(1)
        Layout1.addWidget(self.buttonCancel)
        DPSettingsLayout.addLayout(Layout1)

        self.languageChange()

        self.resize(QSize(562,626).expandedTo(self.minimumSizeHint()))
        self.clearWState(Qt.WState_Polished)

        self.connect(self.buttonOk,SIGNAL("clicked()"),self.accept)
        self.connect(self.buttonCancel,SIGNAL("clicked()"),self.reject)


    def languageChange(self):
        self.setCaption(self.__tr("Dir Project Settings"))
        self.groupBox3_2.setTitle(self.__tr("Global Settings"))
        self.textLabel1.setText(self.__tr("Search Type:"))
        self.w_search_type.clear()
        self.w_search_type.insertItem(self.__tr("Exact"))
        self.w_search_type.insertItem(self.__tr("Character"))
        self.w_search_type.insertItem(self.__tr("Word"))
        self.groupBox2.setTitle(self.__tr("Project Specific Settings"))
        self.textLabel2.setText(self.__tr("Name Filters:"))
        self.w_ignore.setTitle(self.__tr("Ignore Files/Directories"))
        self.buttonOk.setText(self.__tr("&OK"))
        self.buttonOk.setAccel(QKeySequence(QString.null))
        self.buttonCancel.setText(self.__tr("&Cancel"))
        self.buttonCancel.setAccel(QKeySequence(QString.null))


    def __tr(self,s,c = None):
        return qApp.translate("DPSettings",s,c)



class DPSettings(DPSettingsBase):

  SEARCH_TYPES = ['exact', 'char', 'word']
  
  def __init__(self, dp):
    DPSettingsBase.__init__(self,kate.mainWidget(),None,1,0)

    # save an instance of our DirectoryProject
    self.dp = dp


  # overloaded virtual
  def languageChange(self):
    DPSettingsBase.languageChange(self)
    self.w_search_type.clear()
    # make sure this corresponds to the order of DPSettings.SEARCH_TYPES.
    # it won't let me call self.__tr() here and I'm too drunk to figure out why
    self.w_search_type.insertItem("Exact") 
    self.w_search_type.insertItem("Character")
    self.w_search_type.insertItem("Word")
    

  # populate's this dialog widgets with values from the config file
  def loadFromConfig(self):
    self.w_filters.setText(self.dp.get_option('filter'))
    self.w_ignore.clear()
    self.w_ignore.insertStrList( [x.strip() for x in self.dp.get_option('ignore').split(',')] )
    self.w_search_type.setCurrentItem( DPSettings.SEARCH_TYPES.index(self.dp.config.get('general', 'search_type')) )


  # overloaded virtual
  def show(self):
    
    # use the user's preferred size
    x, y = self.dp.config.get('general', 'config_size').split('x')
    x, y = int(x), int(y)
    size = QSize(x,y).expandedTo(self.minimumSizeHint())
    self.resize(size.width(), size.height())

    self.loadFromConfig()

    KDialog.show(self)


  # overloaded virtual (I think?)
  def accept(self):

    # get the old filter and ignore settings
    old_filter = str(QString(self.dp.get_option('filter')).simplifyWhiteSpace())
    old_ignore = ','.join( [x.strip() for x in self.dp.get_option('ignore').split(',')] )

    # dialog size
    self.dp.config.set('general', 'config_size', "%dx%d" % (self.width(), self.height()))

    # search type
    self.dp.config.set('general', 'search_type', DPSettings.SEARCH_TYPES[self.w_search_type.currentItem()])

    # filters
    new_filter = str(self.w_filters.text().simplifyWhiteSpace())
    self.dp.set_option('filter', new_filter)

    # ignore
    new_ignore = ','.join( [ str(s).strip() for s in self.w_ignore.items() ] )
    self.dp.set_option('ignore', new_ignore)

    # save the config
    self.dp.saveConfig()

    # this should close the settings dialog
    QDialog.accept(self)

    # if they changed any settings offer to reload the project
    if old_ignore != new_ignore or old_filter != new_filter:
      result = KMessageBox.questionYesNo(kate.mainWidget(), "Project settings have changed, would you like to reload the project?", "Reload Project?")
      if result == KMessageBox.Yes:
        self.dp.reload()



#######################
# callbacks down here #
#######################

# I guess this is the "onInit" callback?
@kate.onWindowShown
def initDirectoryProjectPlugin():
  global dProject
  sourceTool = kate.gui.Tool("Directory Project", "viewmag", kate.gui.Tool.left)
  dProject = DirectoryProject(sourceTool.widget)
