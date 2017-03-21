// on change of seat, change header and title
var urlInterpret = {
  url: window.location.href,

  getParameterByName : function(name, url) {
      if (!url) {
        url = window.location.href;
      }
      name = name.replace(/[\[\]]/g, "\\$&");
      var regex = new RegExp("[?&]" + name + "(=([^&#]*)|&|#|$)"),
          results = regex.exec(url);
      if (!results) return null;
      if (!results[2]) return '';
      return decodeURIComponent(results[2].replace(/\+/g, " "));
  }
}

var uiAttr = {
  changeNavBar : function(setting){
    $(".navbaractive").removeClass("navbaractive");
    console.log(setting)
    var id = "#nav-" + setting.name;
    console.log(id)
    $(id).addClass("navbaractive");

    $("#pagetitle").html(setting.title);
    document.title = setting.title;

  }
}

function PageSetting(title, name){
	this.title = title
  this.name = name
}

var manchesterGorton = new PageSetting("Manchester, Gorton", "manchester-gorton");

function initialization(){
	var name = urlInterpret.getParameterByName("seat", urlInterpret.url);
  var setting = urlParamMap[name];
  uiAttr.changeNavBar(setting);
}

var urlParamMap = {
  null : manchesterGorton,
  "manchester-gorton" : manchesterGorton
}
