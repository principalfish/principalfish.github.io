var filter = {
  pollsDisplay : true,
  modelDisplay : true,
  movingDisplay: true,

  company : function(value){
    if (companies.indexOf(value) == -1){
      companies.push(value);
    } else {
      var i = companies.indexOf(value);
      companies.splice(i, 1)
    }

    filter.redraw();
  },

  redraw: function(){
    $("#plot g").empty()
    pageLoad();
  }


}
