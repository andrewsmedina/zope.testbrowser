##############################################################################
#
# Copyright (c) 2005 Zope Foundation and Contributors.
# All Rights Reserved.
#
# This software is subject to the provisions of the Zope Public License,
# Version 2.1 (ZPL).  A copy of the ZPL should accompany this distribution.
# THIS SOFTWARE IS PROVIDED "AS IS" AND ANY AND ALL EXPRESS OR IMPLIED
# WARRANTIES ARE DISCLAIMED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF TITLE, MERCHANTABILITY, AGAINST INFRINGEMENT, AND FITNESS
# FOR A PARTICULAR PURPOSE.
#
##############################################################################
"""Webtest-based Functional Doctest interfaces
"""
__docformat__ = "reStructuredText"

import sys
import re
import time
import urlparse
import io
from contextlib import contextmanager
from collections import OrderedDict

from zope.interface import implementer

from zope.testbrowser import interfaces
from zope.testbrowser._compat import httpclient, PYTHON2
import zope.testbrowser.cookies

import webtest

RegexType = type(re.compile(''))
_compress_re = re.compile(r"\s+")
compressText = lambda text: _compress_re.sub(' ', text.strip())


@implementer(interfaces.IBrowser)
class Browser(object):
    """A web user agent."""

    _contents = None
    _counter = 0
    _response = None
    _req_headers = None
    _history = None

    def __init__(self, url=None, wsgi_app=None):
        self.timer = PystoneTimer()
        self.raiseHttpErrors = True
        self.testapp = webtest.TestApp(wsgi_app)
        self._req_headers = {}
        self._history = History()

        if url is not None:
            self.open(url)

    @property
    def url(self):
        """See zope.testbrowser.interfaces.IBrowser"""
        if self._response is None:
            return None
        return self._response.request.url

    @property
    def isHtml(self):
        """See zope.testbrowser.interfaces.IBrowser"""
        return 'html' in self._response.content_type

    @property
    def lastRequestPystones(self):
        """See zope.testbrowser.interfaces.IBrowser"""
        return self.timer.elapsedPystones

    @property
    def lastRequestSeconds(self):
        """See zope.testbrowser.interfaces.IBrowser"""
        return self.timer.elapsedSeconds

    @property
    def title(self):
        """See zope.testbrowser.interfaces.IBrowser"""
        if not self.isHtml:
            raise BrowserStateError('not viewing HTML')

        titles = self._response.html.find_all('title')
        if not titles:
            return None
        return to_str(titles[0].text, self._response)

    def reload(self):
        """See zope.testbrowser.interfaces.IBrowser"""
        if self._response is None:
            raise BrowserStateError("no URL has yet been .open()ed")

        req = self._response.request
        with self._preparedRequest():
            resp = self.testapp.request(req)
            self._setResponse(resp)

    def goBack(self, count=1):
        """See zope.testbrowser.interfaces.IBrowser"""
        resp = self._history.back(count, self._response)
        self._setResponse(resp)

    @property
    def contents(self):
        """See zope.testbrowser.interfaces.IBrowser"""
        if self._response is not None:
            return self._response.body
        else:
            return None
        ## # TODO
        ## if self._contents is not None:
        ##     return self._contents
        ## response = self.mech_browser.response()
        ## if response is None:
        ##     return None
        ## old_location = response.tell()
        ## response.seek(0)
        ## self._contents = response.read()
        ## response.seek(old_location)
        ## return self._contents

    @property
    def headers(self):
        """See zope.testbrowser.interfaces.IBrowser"""
        resptxt = []
        resptxt.append(b'Status: '+self._response.status)
        for h, v in sorted(self._response.headers.items()):
            resptxt.append("%s: %s" % (h, v))

        stream = io.BytesIO(b'\n'.join(resptxt))
        return httpclient.HTTPMessage(stream)

    @property
    def cookies(self):
        if self.url is None:
            raise RuntimeError("no request found")
        return zope.testbrowser.cookies.Cookies(self.testapp, self.url,
                                                self._req_headers)

    HEADER_KEY = 'X-zope-handle-errors'

    @property
    def handleErrors(self):
        headers = self._req_headers
        value = dict(headers).get(self.HEADER_KEY, True)
        return {'False': False}.get(value, True)

    @handleErrors.setter
    def handleErrors(self, value):
        headers = self._req_headers
        current_value = self.handleErrors
        if current_value == value:
            return

        # Remove the current header...
        for key, header_value in headers[:]:
            if key == self.HEADER_KEY:
                headers.remove((key, header_value))
        # ... Before adding the new one.
        headers.append((self.HEADER_KEY, {False: 'False'}.get(value, 'True')))

    def addHeader(self, key, value):
        """See zope.testbrowser.interfaces.IBrowser"""
        if (key.lower() in ('cookie', 'cookie2') and
            self.cookies.header):
            raise ValueError('cookies are already set in `cookies` attribute')
        self._req_headers[key] = value

    def open(self, url, data=None):
        """See zope.testbrowser.interfaces.IBrowser"""

        url = str(url)
        with self._preparedRequest() as reqargs:
            self._history.add(self._response)
            if data is not None:
                resp = self.testapp.post(url, data, **reqargs)
            else:
                resp = self.testapp.get(url, **reqargs)

            self._setResponse(resp)

        # if the headers don't have a status, I suppose there can't be an error
        if 'Status' in self.headers:
            code, msg = self.headers['Status'].split(' ', 1)
            code = int(code)
            if self.raiseHttpErrors and code >= 400:
                raise httpclient.HTTPException(url, code, msg, self.headers)

    def _clickSubmit(self, form, control, coord):
        # TODO: handle coord
        # find index of given control in the form
        with self._preparedRequest() as reqargs:
            self._history.add(self._response)
            try:
                if control:
                    index = form.fields[control.name].index(control)
                    resp = form.submit(control.name, index, **reqargs)
                else:
                    resp = form.submit(**reqargs)

                self._setResponse(resp)

            except Exception as e:
                fix_exception_name(e)
                raise

    def _setResponse(self, response):
        self._response = response
        if self._response.charset is None:
            self._response.charset = 'latin1'

    def getLink(self, text=None, url=None, id=None, index=0):
        """See zope.testbrowser.interfaces.IBrowser"""
        qa = 'a' if id is None else 'a#%s' % id
        qarea = 'aa' if id is None else 'area#%s' % id
        links = self._response.html.select(qa)
        links.extend(self._response.html.select(qarea))

        matching = []
        for elem in links:
            matches = (isMatching(elem.text, text) and
                       isMatching(elem.get('href'), url))

            if matches:
                matching.append(elem)

        if index >= len(matching):
            raise LinkNotFoundError()
        elem = matching[index]

        baseurl = self._getBaseUrl()

        return Link(elem, self, baseurl)

    def follow(self, *args, **kw):
        """Select a link and follow it."""
        self.getLink(*args, **kw).click()


    def _getBaseUrl(self):
        # Look for <base href> tag and use it as base, if it exists
        bases = self._response.html.find_all('base')
        if bases:
            return bases[0]['href']

        # If no base tags found, use last request url as a base
        return self._response.request.url

    def getForm(self, id=None, name=None, action=None, index=None):
        """See zope.testbrowser.interfaces.IBrowser"""
        zeroOrOne([id, name, action], '"id", "name", and "action"')
        matching_forms = []
        allforms = set(self._response.forms.values())
        for form in allforms:
            if ((id is not None and form.id == id)
            or (name is not None and form.html.body.form.get('name') == name)
            or (action is not None and re.search(action, form.action))
            or id == name == action == None):
                matching_forms.append(form)

        if index is None and not any([id, name, action]):
            if len(matching_forms) == 1:
                index = 0
            else:
                raise ValueError(
                    'if no other arguments are given, index is required.')

        form = disambiguate(matching_forms, '', index)
        return Form(self, form)

    def getControl(self, label=None, name=None, index=None):
        """See zope.testbrowser.interfaces.IBrowser"""
        intermediate, msg, available = self._getAllControls(
            label, name, self._response.forms.values(),
            include_subcontrols=True)
        control = disambiguate(intermediate, msg, index,
                               controlFormTupleRepr,
                               available)
        return control


    def _getAllControls(self, label, name, forms, include_subcontrols=False):
        onlyOne([label, name], '"label" and "name"')

        forms = list(forms) # might be an iterator, and we need to iterate twice

        available = None
        if label is not None:
            res = self._findByLabel(label, forms, include_subcontrols)
            msg = 'label %r' % label
        elif name is not None:
            include_subcontrols = False
            res = self._findByName(name, forms)
            msg = 'name %r' % name
        if not res:
            available = list(self._findAllControls(forms, include_subcontrols))
        return res, msg, available


    def _findByLabel(self, label, forms, include_subcontrols=False):
        # forms are iterable of mech_forms
        matches = re.compile(r'(^|\b|\W)%s(\b|\W|$)'
                             % re.escape(normalizeWhitespace(label))).search
        found = []
        for wtcontrol in self._findAllControls(forms, include_subcontrols):
            for l in wtcontrol.getLabels():
                if matches(l):
                    found.append(wtcontrol)
                    break
        return found

    def _indexControls(self, form):
        # Unfortunately, webtest will remove all 'name' attributes from
        # form.html after parsing. But we need them (at least to locate labels
        # for radio buttons). So we are forced to reparse part of html, to
        # extract elements.
        from bs4 import BeautifulSoup
        html = BeautifulSoup(form.text)
        tags = ('input', 'select', 'textarea', 'button')
        return html.find_all(tags)

    def _findByName(self, name, forms):
        return [c for c in self._findAllControls(forms) if c.name == name]


    def _findAllControls(self, forms, include_subcontrols=False):
        for f in forms:
            allelems = self._indexControls(f)

            for cname, wtcontrols in f.fields.items():
                for c in controlFactory(cname, wtcontrols, allelems, self,
                                        include_subcontrols):
                    yield c


    def _changed(self):
        self._counter += 1
        self._contents = None
        self._req_headers = {}

    @contextmanager
    def _preparedRequest(self):
        self.timer.start()
        if self.url:
            self._req_headers['Referer'] = self.url
        kwargs = {'headers': sorted(self._req_headers.items())}

        yield kwargs

        self._changed()
        self.timer.stop()

def controlFactory(name, wtcontrols, elemindex, browser, include_subcontrols=False):
    assert len(wtcontrols) > 0

    first_wtc = wtcontrols[0]
    checkbox = isinstance(first_wtc, webtest.forms.Checkbox)

    # Create control list
    if checkbox:
        ctrlelems = [(wtc, elemindex[wtc.pos]) for wtc in wtcontrols]
        controls = [CheckboxListControl(name, ctrlelems, browser)]

    else:
        controls = []
        for wtc in wtcontrols:
            controls.append(simpleControlFactory(wtc, wtc.form, elemindex, browser))

    # Yield all created controls
    for control in controls:
        yield control

        if include_subcontrols:
            for subcontrol in control.controls:
                yield subcontrol


def simpleControlFactory(wtcontrol, form, elemindex, browser):
    if isinstance(wtcontrol, webtest.forms.Radio):
        elems = [e for e in elemindex
                 if e.attrs.get('name') == wtcontrol.name]
        return RadioListControl(wtcontrol, form, elems, browser)

    elem = elemindex[wtcontrol.pos]
    if isinstance(wtcontrol, (webtest.forms.Select,
                              webtest.forms.MultipleSelect)):
        return ListControl(wtcontrol, form, elem, browser)

    elif isinstance(wtcontrol, webtest.forms.Submit):
        if wtcontrol.attrs.get('type', 'submit') == 'image':
            return ImageControl(wtcontrol, form, elem, browser)
        else:
            return SubmitControl(wtcontrol, form, elem, browser)
    else:
        return Control(wtcontrol, form, elem, browser)

@implementer(interfaces.ILink)
class Link(object):

    def __init__(self, link, browser, baseurl=""):
        self._link = link
        self.browser = browser
        self._baseurl = baseurl
        self._browser_counter = self.browser._counter

    def click(self):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        self.browser.open(self.url)

    @property
    def url(self):
        relurl = self._link['href']
        absurl = urlparse.urljoin(self._baseurl, relurl)
        return str(absurl)

    @property
    def text(self):
        txt = normalizeWhitespace(self._link.text)
        return to_str(txt, self.browser._response)

    @property
    def tag(self):
        return str(self._link.name)

    @property
    def attrs(self):
        r = self.browser._response
        return {to_str(k, r): to_str(v, r)
                for k, v in self._link.attrs.items()}

    def __repr__(self):
        return "<%s text='%s' url='%s'>" % (
            self.__class__.__name__, normalizeWhitespace(self.text), self.url)

def controlFormTupleRepr(wtcontrol):
    return wtcontrol.mechRepr()


@implementer(interfaces.IControl)
class Control(object):
    def __init__(self, control, form, elem, browser):
        self._control = control
        self._form = form
        self._elem = elem
        self.browser = browser
        self._browser_counter = self.browser._counter

    @property
    def disabled(self):
        return 'disabled' in self._control.attrs

    @property
    def type(self):
        return to_str(self._control.attrs.get('type', None),
                      self.browser._response)

    @property
    def name(self):
        return to_str(self._control.name, self.browser._response)

    @property
    def multiple(self):
        return 'multiple' in self._control.attrs

    @property
    def value(self):
        if (self.type == 'checkbox' and
            len(self.mech_control.items) == 1 and  # TODO
            self.mech_control.items[0].name == 'on'):  # TODO

            return self.mech_control.items[0].selected  # TODO

        if isinstance(self._control, webtest.forms.Submit):
            return str(self._control.value_if_submitted())

        val = self._control.value
        if val is None:
            return None
        # Remove first newline character
        if val.startswith('\n'):
            val = val[1:]

        return str(val)

    @value.setter
    def value(self, value):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        if self.type == 'file':
            self.add_file(value, content_type=self.content_type,
                          filename=self.filename)
        elif self.type == 'checkbox' and len(self.mech_control.items) == 1:
            # TODO
            self.mech_control.items[0].selected = bool(value)
        else:
            self._control.value = value

    def add_file(self, file, content_type, filename):
        if self.type != 'file':
            raise TypeError("Can't call add_file on %s controls"
                            % self.mech_control.type)

        if isinstance(file, io.IOBase):
            contents = file.read()
        else:
            contents = file

        # XXX: webtest relies on mimetypes.guess_type to get mime type of
        # upload file and doesn't let to set it explicitly, so we are ignoring
        # content_type parameter here. If it is still unacceptable, consider
        # using mock.object to force mimetypes to return "right" type.
        self._form[self.name] = webtest.forms.Upload(filename, contents)

    def clear(self):
        # TODO
        if self._browser_counter != self.browser._counter:
            raise zope.testbrowser.interfaces.ExpiredError
        self.mech_control.clear()

    def __repr__(self):
        return "<%s name='%s' type='%s'>" % (
            self.__class__.__name__, self.name, self.type)

    def getLabels(self):
        return getControlLabels(self._elem, self._form.html)

    @property
    def controls(self):
        return []

    def mechRepr(self):
        # emulate mechanize control representation
        ctrl = self._control
        if isinstance(ctrl, webtest.forms.Text):
            tp = ctrl.attrs.get('type')
            infos = []
            if 'readonly' in ctrl.attrs or tp == 'hidden':
                infos.append('readonly')
            if 'disabled' in ctrl.attrs:
                infos.append('disabled')

            classnames = {'password': "PasswordControl",
                          'hidden': "HiddenControl"
                          }
            clname = classnames.get(tp, "TextControl")
            return "<%s(%s=%s)%s>" % (clname, ctrl.name, ctrl.value,
                                      ' (%s)' % (', '.join(infos)) if infos else '')

        if isinstance(ctrl, webtest.forms.File):
            return repr(ctrl) + "<-- unknown"
        raise NotImplementedError(str((self, ctrl)))

@implementer(interfaces.ISubmitControl)
class SubmitControl(Control):

    def click(self):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        self.browser._clickSubmit(self._form, self._control, (1,1))

    def getLabels(self):
        labels = super(SubmitControl, self).getLabels()
        labels.append(self._control.value_if_submitted())
        return labels

    def mechRepr(self):
        return "ImageControl???"

@implementer(interfaces.IListControl)
class ListControl(Control):

    @property
    def type(self):
        if isinstance(self._control, webtest.forms.Radio):
            return 'radio'
        return 'select'

    @property
    def value(self):
        val = self._control.value
        if val is None:
            return []

        r = self.browser._response
        return [to_str(v, r) for v in val]

    @value.setter
    def value(self, value):
        self._control.value = value

    @property
    def displayValue(self):
        """See zope.testbrowser.interfaces.IListControl"""
        # not implemented for anything other than select;
        # would be nice if mechanize implemented for checkbox and radio.
        # attribute error for all others.

        if self._control.value is None:
            return []

        titles = []
        for key, title, elem in self._getOptions():
            if key in self._control.value:
                titles.append(title)
        r = self.browser._response
        return [to_str(t, r) for t in titles]

    @displayValue.setter
    def displayValue(self, value):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError

        values = []
        for key, title, elem in self._getOptions():
            if title in value:
                values.append(key)
        self._control.value = values

    @property
    def displayOptions(self):
        """See zope.testbrowser.interfaces.IListControl"""
        return [to_str(title, self.browser._response)
                for key, title, elem in self._getOptions()]

    @property
    def options(self):
        """See zope.testbrowser.interfaces.IListControl"""
        return [key for key, title, elem in self._getOptions()]

    def getControl(self, label=None, value=None, index=None):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError

        onlyOne([label, value], '"label" and "value"')

        if label is not None:
            options = [c for c in self.controls
                       if label in c.getLabels()]
            msg = 'label %r' % label
        elif value is not None:
            options = [c for c in self.controls
                       if isMatching(c.value, value)]
            msg = 'value %r' % value

        res = disambiguate(options, msg, index, controlFormTupleRepr)
        return res

    @property
    def controls(self):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        ctrls = []
        for key, value, elem in self._getOptions():
            ctrls.append(ItemControl(self, elem, self._form, self.browser))

        return ctrls

    def _getOptions(self):
        return [(e.attrs.get('value'), e.attrs.get('label', e.text), e)
                for e in self._elem.select('option')]

    def mechRepr(self):
        return "<SelectControl(%s=[*, ambiguous])>" % self.name

@implementer(interfaces.IListControl)
class RadioListControl(ListControl):
    def __init__(self, control, form, elems, browser):
        super(RadioListControl, self).__init__(control, form, elems[0], browser)
        self._elems = elems

    def __repr__(self):
        # Return backwards compatible representation
        return "<ListControl name='%s' type='radio'>" % self.name

    @property
    def controls(self):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        for opt in self._elems:
            yield RadioItemControl(self, opt, self._form, self.browser)


@implementer(interfaces.IListControl)
class CheckboxListControl(object):
    def __init__(self, name, ctrlelems, browser):
        self.name = name
        self.browser = browser
        self._ctrlelems = ctrlelems

    @property
    def options(self):
        # TODO
        pass

    @property
    def displayOptions(self):
        # TODO
        pass

    @property
    def displayValue(self):
        # TODO
        pass

    def getControl(label=None, value=None, index=None):
        #TODO
        pass

    @property
    def controls(self):
        return [CheckboxItemControl(self, c, e, c.form, self.browser)
                for c, e in self._ctrlelems]

    def mechRepr(self):
        return "<SelectControl(%s=[*, ambiguous])>" % self.name

    def getLabels(self):
        return []

    def __repr__(self):
        # Return backwards compatible representation
        return "<ListControl name='%s' type='checkbox'>" % self.name

@implementer(interfaces.IImageSubmitControl)
class ImageControl(Control):

    def click(self, coord=(1,1)):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        self.browser._clickSubmit(self._form, self._control, coord)

    def mechRepr(self):
        return "ImageControl???"


@implementer(interfaces.IItemControl)
class ItemControl(object):

    def __init__(self, parent, elem, form, browser):
        self._parent = parent
        self._elem = elem
        self._form = form
        self.browser = browser
        self._browser_counter = self.browser._counter
        self._enable_setattr_errors = True

    @property
    def control(self):
        # TODO
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        res = controlFactory(
            self._item._control, self.mech_form, self.browser)
        self.__dict__['control'] = res
        return res

    @property
    def disabled(self):
        # TODO
        return self.mech_item.disabled

    @property
    def selected(self):
        """See zope.testbrowser.interfaces.IControl"""
        return self._elem.attrs.get('value') in self._parent.value

    @selected.setter
    def selected(self, value):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        self._parent.value = self._elem.attrs.get('value')

    @property
    def optionValue(self):
        return to_str(self._elem.attrs.get('value'), self.browser._response)

    def click(self):
        # TODO
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        self.mech_item.selected = not self.mech_item.selected

    def __repr__(self):
        return "<ItemControl name='%s' type='select' optionValue=%r selected=%r>" % \
                (self._parent.name, self.optionValue, self.selected)

    def getLabels(self):
        lbl = self._elem.attrs.get('label', self._elem.text)
        labels = [self._elem.attrs.get('label'), self._elem.text]
        return [normalizeWhitespace(lbl) for lbl in labels if lbl]

    def mechRepr(self):
        contents = normalizeWhitespace(self._elem.text)
        id = self._elem.attrs.get('id')
        label = self._elem.attrs.get('label', contents)
        value = self._elem.attrs.get('value')
        name = self._elem.attrs.get('name', value)
        return "<Item name='%s' id=%s contents='%s' value='%s' label='%s'>" % \
                (name, id, contents, value, label)

class RadioItemControl(ItemControl):
    @property
    def selected(self):
        """See zope.testbrowser.interfaces.IControl"""
        return self._elem.attrs.get('value') in self._parent.value

    @selected.setter
    def selected(self, value):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        self._parent.value = self._elem.attrs.get('value')

    @property
    def optionValue(self):
        return to_str(self._elem.attrs.get('value'), self.browser._response)

    def getLabels(self):
        return getControlLabels(self._elem, self._form.html)

    def __repr__(self):
        return "<ItemControl name='%s' type='radio' optionValue=%r selected=%r>" % (
            self._parent.name, self.optionValue, self.selected)

    def mechRepr(self):
        id = self._elem.attrs.get('id')
        value = self._elem.attrs.get('value')
        name = self._elem.attrs.get('name')

        r = self.browser._response
        props = []
        if self._elem.parent.name == 'label':
            props.append(('__label', {'__text': to_str(self._elem.parent.text, r)}))
        if self.selected:
            props.append(('checked', 'checked'))
        props.append(('type', 'radio'))
        props.append(('name', name))
        props.append(('value', value))
        props.append(('id', id))

        propstr = ' '.join('%s=%r' % (pk, pv) for pk, pv in props)
        return "<Item name='%s' id='%s' %s>" % \
                (value, id, propstr)

class CheckboxItemControl(ItemControl):
    def __init__(self, parent, wtcontrol, elem, form, browser):
        super(CheckboxItemControl, self).__init__(parent, elem, form, browser)
        self._control = wtcontrol

    @property
    def selected(self):
        """See zope.testbrowser.interfaces.IControl"""
        return self._control.value == self._elem.attrs.get('value')

    @selected.setter
    def selected(self, value):
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        self._control.value = self._elem.attrs.get('value')

    @property
    def optionValue(self):
        return to_str(self._control.value, self.browser._response)

    def getLabels(self):
        return getControlLabels(self._elem, self._form.html)

    def __repr__(self):
        return "<ItemControl name='%s' type='checkbox' optionValue=%r selected=%r>" % (
            self._control.name, self.optionValue, self.selected)

    def mechRepr(self):
        id = self._elem.attrs.get('id')
        value = self._elem.attrs.get('value')
        name = self._elem.attrs.get('name')

        r = self.browser._response
        props = []
        if self._elem.parent.name == 'label':
            props.append(('__label', {'__text': to_str(self._elem.parent.text, r)}))
        if self.selected:
            props.append(('checked', 'checked'))
        props.append(('name', name))
        props.append(('type', 'checkbox'))
        props.append(('id', id))
        props.append(('value', value))

        propstr = ' '.join('%s=%r' % (pk, pv) for pk, pv in props)
        return "<Item name='%s' id='%s' %s>" % \
                (value, id, propstr)

@implementer(interfaces.IForm)
class Form(object):
    """HTML Form"""

    def __init__(self, browser, form):
        """Initialize the Form

        browser - a Browser instance
        form - a mechanize.HTMLForm instance
        """
        self.browser = browser
        self._form = form
        self._browser_counter = self.browser._counter

    @property
    def action(self):
        return self._form.action

    @property
    def method(self):
        return self._form.method

    @property
    def enctype(self):
        return self._form.enctype

    @property
    def name(self):
        return self._form.html.body.form.get('name')

    @property
    def id(self):
        """See zope.testbrowser.interfaces.IForm"""
        return self._form.id

    def submit(self, label=None, name=None, index=None, coord=(1,1)):
        """See zope.testbrowser.interfaces.IForm"""
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError

        form = self._form
        if label is not None or name is not None:
            controls, msg, available = self.browser._getAllControls(
                label, name, [form])
            controls = [(control, form) for (control, form) in controls
                        if isinstance(control, webtest.forms.Submit)]
            control, form = disambiguate(controls, msg, index,
                                         controlFormTupleRepr,
                                         available)
            self.browser._clickSubmit(form, control, coord)

        else: # JavaScript sort of submit
            if index is not None or coord != (1,1):
                raise ValueError(
                    'May not use index or coord without a control')
            self.browser._clickSubmit(form)
            ## # TODO
            ## request = self.mech_form._switch_click("request", mechanize.Request)
            ## self.browser._start_timer()
            ## with self.browser.timer:
            ##     try:
            ##         form.submit()
            ##         self.browser.mech_browser.open(request)
            ##     except Exception as e:
            ##         fix_exception_name(e)
            ##         raise


    def getControl(self, label=None, name=None, index=None):
        """See zope.testbrowser.interfaces.IBrowser"""
        # TODO
        if self._browser_counter != self.browser._counter:
            raise interfaces.ExpiredError
        intermediate, msg, available = self.browser._get_all_controls(
            label, name, (self.mech_form,), include_subcontrols=True)
        control, form = disambiguate(intermediate, msg, index,
                                     controlFormTupleRepr,
                                     available)
        return controlFactory(control, form, self.browser)

def disambiguate(intermediate, msg, index, choice_repr=None, available=None):
    if intermediate:
        if index is None:
            if len(intermediate) > 1:
                if choice_repr:
                    msg += ' matches:' + ''.join([
                                '\n  %s' % choice_repr(choice)
                                for choice in intermediate])
                raise AmbiguityError(msg)
            else:
                return intermediate[0]
        else:
            try:
                return intermediate[index]
            except IndexError:
                msg = '%s\nIndex %d out of range, available choices are 0...%d' % (
                            msg, index, len(intermediate) - 1)
                if choice_repr:
                    msg += ''.join(['\n  %d: %s' % (n, choice_repr(choice))
                                    for n, choice in enumerate(intermediate)])
    else:
        if available:
            msg += '\navailable items:' + ''.join([
                '\n  %s' % choice_repr(choice)
                for choice in available])
        elif available is not None: # empty list
            msg += '\n(there are no form items in the HTML)'
    raise LookupError(msg)


def onlyOne(items, description):
    total = sum([bool(i) for i in items])
    if total == 0 or total > 1:
        raise ValueError(
            "Supply one and only one of %s as arguments" % description)

def zeroOrOne(items, description):
    if sum([bool(i) for i in items]) > 1:
        raise ValueError(
            "Supply no more than one of %s as arguments" % description)

def getControlLabels(celem, html):
        labels = []

        # find all labels, connected by 'for' attribute
        controlid = celem.attrs.get('id')
        if controlid:
            forlbls = html.select('label[for=%s]' % controlid)
            labels.extend([normalizeWhitespace(l.text) for l in forlbls])

        if celem.parent.name == 'label':
            labels.extend([normalizeWhitespace(celem.parent.text)])

        return [l for l in labels if l is not None]

def normalizeWhitespace(string):
    return ' '.join(string.split())

def isMatching(string, expr):
    """Determine whether ``expr`` matches to ``string``

    ``expr`` can be None, plain text or regular expression.

      * If ``expr`` is ``None``, ``string`` is considered matching
      * If ``expr`` is plain text, its equality to ``string`` will be checked
      * If ``expr`` is regexp, regexp matching agains ``string`` will
        be performed
    """
    if expr is None:
        return True

    if isinstance(expr, RegexType):
        return expr.match(normalizeWhitespace(string))
    else:
        return normalizeWhitespace(expr) in normalizeWhitespace(string)


class PystoneTimer(object):
    start_time = 0
    end_time = 0
    _pystones_per_second = None

    @property
    def pystonesPerSecond(self):
        """How many pystones are equivalent to one second on this machine"""

        # deferred import as workaround for Zope 2 testrunner issue:
        # http://www.zope.org/Collectors/Zope/2268
        from test import pystone
        if self._pystones_per_second == None:
            self._pystones_per_second = pystone.pystones(pystone.LOOPS/10)[1]
        return self._pystones_per_second

    def _getTime(self):
        if sys.platform.startswith('win'):
            # Windows' time.clock gives us high-resolution wall-time
            return time.clock()
        else:
            # everyone else uses time.time
            return time.time()

    def start(self):
        """Begin a timing period"""
        self.start_time = self._getTime()
        self.end_time = None

    def stop(self):
        """End a timing period"""
        self.end_time = self._getTime()

    @property
    def elapsedSeconds(self):
        """Elapsed time from calling `start` to calling `stop` or present time

        If `stop` has been called, the timing period stopped then, otherwise
        the end is the current time.
        """
        if self.end_time is None:
            end_time = self._getTime()
        else:
            end_time = self.end_time
        return end_time - self.start_time

    @property
    def elapsedPystones(self):
        """Elapsed pystones in timing period

        See elapsed_seconds for definition of timing period.
        """
        return self.elapsedSeconds * self.pystonesPerSecond


    def __enter__(self):
        self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()

class History:
    """

    Though this will become public, the implied interface is not yet stable.

    """
    def __init__(self):
        self._history = []  # LIFO

    def add(self, response):
        self._history.append(response)

    def back(self, n, _response):
        response = _response
        while n > 0 or response is None:
            try:
                response = self._history.pop()
            except IndexError:
                raise BrowserStateError("already at start of history")
            n -= 1
        return response

    def clear(self):
        del self._history[:]

class AmbiguityError(ValueError):
    pass

def fix_exception_name(e):
    pass

def to_str(s, response):
    if PYTHON2 and not isinstance(s, bytes):
        return s.encode(response.charset)
    return s

class BrowserStateError(Exception):
    pass

class LinkNotFoundError(IndexError):
    pass
